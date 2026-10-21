"""Publicador próprio do Quintal (grátis): publica no Instagram e no Facebook pela API oficial da Meta.

Roda no GitHub Actions a cada 10 minutos (.github/workflows/publicar.yml). Lê as filas em fila/AAAA-MM-DD.json,
publica o que já chegou na hora e grava o resultado de volta no mesmo arquivo.

Segredo necessário no repositório: META_TOKEN = token de PÁGINA (não expira) da página Quintal de Negócios.
Uso local:  META_TOKEN=... python3 publicador/publicar.py [--teste] [--arquivo fila/AAAA-MM-DD.json]
  --teste: só confere o token, a página e o Instagram; não publica nada.

Item da fila:
  {"id": "...", "quando": "2026-10-20T07:30:00-03:00", "tipo": "carrossel" | "story" | "reel",
   "redes": ["instagram", "facebook"], "midia": ["https://...jpg", ...], "capa": "https://...jpg" (reel, opcional),
   "legenda": "...", "colaboradores": ["marcus_bizutti"]}
O publicador acrescenta: "feito": {"instagram": "<id>", ...}, "erros": {"instagram": "..."}, "tentativas": n.
"""
import datetime as dt, glob, json, os, sys, time, urllib.error, urllib.parse, urllib.request

API = "https://graph.facebook.com/v23.0"
RUPLOAD = "https://rupload.facebook.com"
PAGINA_ID = "1057856840751776"   # Facebook: Quintal de Negócios
TENTATIVAS = 3                   # depois disso o item fica como falhou
ATRASO_MAX = dt.timedelta(hours=12)  # mais atrasado que isso não publica (evita post fora de contexto)
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ErroMeta(Exception):
    pass


def chamar(metodo, url, dados=None, cabecalhos=None, corpo=None, tempo=120):
    if dados is not None:
        corpo = urllib.parse.urlencode(dados).encode()
    req = urllib.request.Request(url, data=corpo, method=metodo, headers=cabecalhos or {})
    try:
        with urllib.request.urlopen(req, timeout=tempo) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            corpo = json.loads(txt)
            erro = corpo.get("error", {}) if isinstance(corpo, dict) else {}
            if isinstance(erro, dict) and erro.get("message"):  # Meta: {"error": {"message", "code", ...}}
                txt = f"{erro.get('message')} (code {erro.get('code')}, subcode {erro.get('error_subcode')})"
            elif isinstance(erro, str):  # Google OAuth: {"error": "invalid_client", "error_description": "..."}
                txt = f"{erro}: {corpo.get('error_description', '')}"
            # senão (ex.: rupload devolve {"debug_info": {...}}) fica o corpo inteiro em txt
        except ValueError:
            pass
        raise ErroMeta(f"HTTP {e.code}: {txt}") from None


class Meta:
    def __init__(self, token):
        self.token = token
        pag = self.get(PAGINA_ID, fields="name,instagram_business_account{id,username}")
        self.pagina = pag["name"]
        ig = pag.get("instagram_business_account") or {}
        self.ig, self.ig_nome = ig.get("id"), ig.get("username")

    def get(self, caminho, **q):
        q["access_token"] = self.token
        return chamar("GET", f"{API}/{caminho}?{urllib.parse.urlencode(q)}")

    def post(self, caminho, **dados):
        dados["access_token"] = self.token
        return chamar("POST", f"{API}/{caminho}", dados={k: v for k, v in dados.items() if v is not None})

    # ---------- Instagram ----------
    def ig_esperar(self, cid, limite=600):
        fim = time.time() + limite
        while time.time() < fim:
            st = self.get(cid, fields="status_code,status").get("status_code")
            if st == "FINISHED":
                return
            if st in ("ERROR", "EXPIRED"):
                raise ErroMeta(f"Instagram recusou a mídia ({st}): {self.get(cid, fields='status').get('status')}")
            time.sleep(8)
        raise ErroMeta("Instagram demorou demais para processar a mídia")

    def ig_video(self, url, **campos):
        """Contêiner de vídeo com envio pelo próprio publicador (não depende do tipo de arquivo do servidor)."""
        c = self.post(f"{self.ig}/media", upload_type="resumable", **campos)["id"]
        dados = baixar(url)
        chamar("POST", f"{RUPLOAD}/ig-api-upload/v23.0/{c}", corpo=dados, tempo=600,
               cabecalhos={"Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(len(dados))})
        self.ig_esperar(c)
        return c

    def ig_publicar(self, cid):
        return self.post(f"{self.ig}/media_publish", creation_id=cid)["id"]

    def ig_carrossel(self, item):
        filhos = []
        for url in item["midia"]:
            filhos.append(self.post(f"{self.ig}/media", image_url=url, is_carousel_item="true")["id"])
        for f in filhos:
            self.ig_esperar(f, 180)
        colab = json.dumps(item.get("colaboradores") or []) if item.get("colaboradores") else None
        c = self.post(f"{self.ig}/media", media_type="CAROUSEL", children=",".join(filhos),
                      caption=item["legenda"], collaborators=colab)["id"]
        self.ig_esperar(c, 180)
        return self.ig_publicar(c)

    def ig_reel(self, item):
        colab = json.dumps(item.get("colaboradores") or []) if item.get("colaboradores") else None
        c = self.ig_video(item["midia"][0], media_type="REELS", caption=item["legenda"], share_to_feed="true",
                          cover_url=item.get("capa"), collaborators=colab)
        return self.ig_publicar(c)

    def ig_story(self, item):
        url = item["midia"][0]
        if url.lower().endswith(".mp4"):
            c = self.ig_video(url, media_type="STORIES")
        else:
            c = self.post(f"{self.ig}/media", media_type="STORIES", image_url=url)["id"]
            self.ig_esperar(c, 180)
        return self.ig_publicar(c)

    # ---------- Facebook ----------
    def fb_carrossel(self, item):
        fotos = [self.post(f"{PAGINA_ID}/photos", url=u, published="false")["id"] for u in item["midia"]]
        anexos = json.dumps([{"media_fbid": f} for f in fotos])
        return self.post(f"{PAGINA_ID}/feed", message=item["legenda"], attached_media=anexos)["id"]

    def fb_video(self, rota, url, **fim):
        ini = self.post(f"{PAGINA_ID}/{rota}", upload_phase="start")
        vid = ini["video_id"]
        dados = baixar(url)
        chamar("POST", ini.get("upload_url") or f"{RUPLOAD}/video-upload/v23.0/{vid}", corpo=dados, tempo=600,
               cabecalhos={"Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(len(dados))})
        r = self.post(f"{PAGINA_ID}/{rota}", upload_phase="finish", video_id=vid, **fim)
        return r.get("post_id") or vid

    def fb_reel(self, item):
        return self.fb_video("video_reels", item["midia"][0], video_state="PUBLISHED", description=item["legenda"])

    def fb_story(self, item):
        url = item["midia"][0]
        if url.lower().endswith(".mp4"):
            return self.fb_video("video_stories", url)
        foto = self.post(f"{PAGINA_ID}/photos", url=url, published="false")["id"]
        return self.post(f"{PAGINA_ID}/photo_stories", photo_id=foto).get("post_id", foto)


class YouTube:
    """Shorts no canal do Quintal (YouTube Data API v3). Segredos: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN."""
    def __init__(self, cid, segredo, refresh):
        r = chamar("POST", "https://oauth2.googleapis.com/token", dados={
            "client_id": cid, "client_secret": segredo, "refresh_token": refresh, "grant_type": "refresh_token"})
        self.token = r["access_token"]

    def reel(self, item):
        titulo = (item.get("titulo") or item["legenda"].split("\n")[0])[:90].rstrip() + " #Shorts"
        meta = {"snippet": {"title": titulo[:100], "description": item["legenda"][:4900], "categoryId": "22",
                            "defaultLanguage": "pt-BR", "tags": ["quintaldenegocios", "Shorts"]},
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
        dados = baixar(item["midia"][0])
        req = urllib.request.Request(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            data=json.dumps(meta).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(dados))})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                destino = r.headers["Location"]
        except urllib.error.HTTPError as e:
            raise ErroMeta(f"YouTube HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None
        return chamar("PUT", destino, corpo=dados, tempo=600, cabecalhos={"Content-Type": "video/mp4"})["id"]


def baixar(url):
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def agora():
    return dt.datetime.now(dt.timezone.utc)


def publicar_item(meta, item, yt=None):
    item.setdefault("feito", {})
    item["erros"] = {}
    for rede in item["redes"]:
        if rede in item["feito"] or (rede == "youtube" and not yt):
            continue
        if rede == "instagram" and not meta.ig:
            item["erros"][rede] = "página sem Instagram profissional ligado"
            continue
        try:
            if rede == "youtube":
                item["feito"][rede] = yt.reel(item)
            else:
                item["feito"][rede] = getattr(meta, f"{'ig' if rede == 'instagram' else 'fb'}_{item['tipo']}")(item)
            print(f"  ok {rede}: {item['feito'][rede]}")
        except Exception as e:  # registra e segue para a próxima rede
            item["erros"][rede] = str(e)[:500]
            print(f"  ERRO {rede}: {e}")
    if not item["erros"]:
        item.pop("erros")
    item["tentativas"] = item.get("tentativas", 0) + 1
    item["publicado_em"] = agora().isoformat(timespec="seconds")


REDES_ATIVAS = {"instagram", "facebook"}


def pendente(item):
    return any(r not in item.get("feito", {}) for r in item["redes"] if r in REDES_ATIVAS) and item.get("tentativas", 0) < TENTATIVAS \
        and not item.get("pular")


def main():
    token = os.environ.get("META_TOKEN", "").strip()
    if not token:
        print("Falta o segredo META_TOKEN (Settings > Secrets and variables > Actions). Nada publicado.")
        return 0
    try:
        meta = Meta(token)
    except ErroMeta as e:  # token vencido ou inválido: avisa no log sem falhar a cada 10 min (evita e-mail repetido)
        print(f"TOKEN DA META COM PROBLEMA, nada publicado: {e}")
        return 0
    print(f"Página: {meta.pagina} | Instagram: @{meta.ig_nome or '(não ligado)'}")
    yt = None
    if all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")):
        try:
            yt = YouTube(os.environ["YT_CLIENT_ID"], os.environ["YT_CLIENT_SECRET"], os.environ["YT_REFRESH_TOKEN"])
            REDES_ATIVAS.add("youtube")
            print("YouTube: conectado")
        except ErroMeta as e:
            print(f"YOUTUBE COM PROBLEMA (segue sem YouTube): {e}")
    else:
        print("YouTube: sem segredos YT_* (segue sem YouTube)")
    if "--teste" in sys.argv:
        return 0
    arquivos = [sys.argv[sys.argv.index("--arquivo") + 1]] if "--arquivo" in sys.argv else \
        sorted(glob.glob(os.path.join(RAIZ, "fila", "20*.json")))
    falhas = []
    for arq in arquivos:
        fila = json.load(open(arq, encoding="utf-8"))
        mudou = False
        for item in sorted(fila, key=lambda i: i["quando"]):
            quando = dt.datetime.fromisoformat(item["quando"])
            if not pendente(item) or quando > agora():
                continue
            if agora() - quando > ATRASO_MAX:
                item["pular"] = "passou mais de 12 h do horário"
                mudou = True
                continue
            print(f"{item['id']} ({item['tipo']}, {item['quando']})")
            publicar_item(meta, item, yt)
            mudou = True
            if item.get("erros") and item["tentativas"] >= TENTATIVAS:
                falhas.append(f"{item['id']}: {item['erros']}")
            json.dump(fila, open(arq, "w", encoding="utf-8"), ensure_ascii=False, indent=1)  # salva a cada item
        if mudou:
            json.dump(fila, open(arq, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if falhas:  # falha o job: o GitHub manda e-mail ao dono do repositório
        print("FALHARAM DE VEZ:\n" + "\n".join(falhas))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
