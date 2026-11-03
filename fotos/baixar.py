"""Roda no GitHub Actions: baixa as fotos pedidas em fotos/pedidos/*.txt para fotos/cache/<chave>.jpg.

Cada pedido tem uma referência: uma URL de imagem ou "commons:Nome do arquivo.jpg" (Wikimedia Commons).
Da Wikimedia grava também <chave>.json com autor, licença e página, para conferir o crédito.
O pedido é apagado ao final; o que falhar fica registrado em fotos/cache/<chave>.erro.txt.
"""
import glob, hashlib, io, json, os, re, time, urllib.parse, urllib.request
from PIL import Image

UA = "QuintalPostsBot/1.0 (https://github.com/richardbatistacomunicacao-del/quintal-posts-midia)"
CACHE = "fotos/cache"


def pega(url, tent=4):
    for i in range(tent):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": f"https://{urllib.parse.urlparse(url).netloc}/"})
            with urllib.request.urlopen(req, timeout=30) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or i == tent - 1: raise
            time.sleep(15 * (i + 1))


def commons(nome):
    api = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|extmetadata",
        "iiurlwidth": 1200, "titles": "File:" + nome})
    pag = next(iter(json.loads(pega(api))["query"]["pages"].values()))
    if "imageinfo" not in pag: raise ValueError("arquivo não existe na Wikimedia Commons")
    ii = pag["imageinfo"][0]; m = ii.get("extmetadata", {})
    limpa = lambda k: re.sub(r"<[^>]+>", "", m.get(k, {}).get("value", "")).strip()
    meta = {"autor": limpa("Artist"), "licenca": limpa("LicenseShortName"), "pagina": ii.get("descriptionurl")}
    return ii.get("thumburl") or ii["url"], meta


def jpg(dados):
    im = Image.open(io.BytesIO(dados))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA"); fundo = Image.new("RGB", im.size, "white"); fundo.paste(im, mask=im.split()[-1]); im = fundo
    else:
        im = im.convert("RGB")
    if im.width > 1600: im = im.resize((1600, round(im.height * 1600 / im.width)))
    b = io.BytesIO(); im.save(b, "JPEG", quality=88); return b.getvalue()


os.makedirs(CACHE, exist_ok=True)
for ped in sorted(glob.glob("fotos/pedidos/*.txt")):
    ref = open(ped).read().strip()
    k = hashlib.md5(ref.encode()).hexdigest()[:12]
    try:
        meta = {"referencia": ref}
        if ref.startswith("commons:"):
            url, m = commons(ref[8:].strip().replace(" ", "_")); meta.update(m)
        else:
            url = ref
        open(f"{CACHE}/{k}.jpg", "wb").write(jpg(pega(url)))
        json.dump(meta, open(f"{CACHE}/{k}.json", "w"), ensure_ascii=False, indent=1)
        if os.path.exists(f"{CACHE}/{k}.erro.txt"): os.remove(f"{CACHE}/{k}.erro.txt")
        print("ok  ", k, ref)
    except Exception as e:
        open(f"{CACHE}/{k}.erro.txt", "w").write(f"{ref}\n{type(e).__name__}: {e}\n")
        print("ERRO", k, ref, e)
    os.remove(ped)
    time.sleep(2)
