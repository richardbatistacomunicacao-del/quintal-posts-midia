"""Regressão: erro do Google (error em texto) não pode derrubar o publicador. Rodar: python3 -m unittest discover publicador/testes"""
import io, json, os, sys, unittest, urllib.error
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import publicar


def falha(codigo, corpo):
    def _urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, codigo, "erro", {}, io.BytesIO(json.dumps(corpo).encode()))
    return _urlopen


class TestChamar(unittest.TestCase):
    def test_erro_google_em_texto_vira_ErroMeta(self):
        corpo = {"error": "invalid_client", "error_description": "Unauthorized"}
        with mock.patch("urllib.request.urlopen", falha(401, corpo)):
            with self.assertRaises(publicar.ErroMeta) as c:
                publicar.chamar("POST", "https://oauth2.googleapis.com/token", dados={"a": "b"})
        self.assertIn("invalid_client", str(c.exception))

    def test_erro_meta_em_objeto_continua_igual(self):
        corpo = {"error": {"message": "Session has expired", "code": 190, "error_subcode": 463}}
        with mock.patch("urllib.request.urlopen", falha(400, corpo)):
            with self.assertRaises(publicar.ErroMeta) as c:
                publicar.chamar("GET", "https://graph.facebook.com/x")
        self.assertIn("code 190", str(c.exception))

    def test_youtube_com_credencial_invalida_nao_derruba_main(self):
        corpo = {"error": "invalid_client", "error_description": "Unauthorized"}
        with mock.patch("urllib.request.urlopen", falha(401, corpo)):
            with self.assertRaises(publicar.ErroMeta):
                publicar.YouTube("id", "segredo", "refresh")


if __name__ == "__main__":
    unittest.main()
