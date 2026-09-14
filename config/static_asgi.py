"""Handler ASGI mínimo para /static/* (WhiteNoise é WSGI-only).

Uso pessoal local: serve arquivos coletados em STATIC_ROOT. Sem traversal
de diretório, com content-type por extensão. Qualquer outra rota vai ao Django.
"""

from pathlib import Path

from asgiref.sync import sync_to_async


def static_wrapper(django_app, static_root: Path, url_prefix: str = "/static/"):
    static_root = static_root.resolve()

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(url_prefix):
            rel = scope["path"][len(url_prefix) :].lstrip("/")
            target = (static_root / rel).resolve()
            if rel and str(target).startswith(str(static_root)) and target.is_file():
                import mimetypes

                ctype, _ = mimetypes.guess_type(str(target))
                body = await sync_to_async(target.read_bytes)()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", (ctype or "application/octet-stream").encode()),
                            (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"public, max-age=60"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"static not found"})
            return
        await django_app(scope, receive, send)

    return app
