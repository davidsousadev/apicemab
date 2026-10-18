"""Inicialização preguiçosa do Firebase Admin SDK.

As variáveis de ambiente usam prefixo FIREBASE_, mas credentials.Certificate
precisa receber as chaves oficiais do JSON de service account (sem o prefixo).
"""
from decouple import config
import firebase_admin
from firebase_admin import credentials


_ENV_TO_CREDENTIAL = {
    "type": "FIREBASE_TYPE",
    "project_id": "FIREBASE_PROJECT_ID",
    "private_key_id": "FIREBASE_PRIVATE_KEY_ID",
    "private_key": "FIREBASE_PRIVATE_KEY",
    "client_email": "FIREBASE_CLIENT_EMAIL",
    "client_id": "FIREBASE_CLIENT_ID",
    "auth_uri": "FIREBASE_AUTH_URI",
    "token_uri": "FIREBASE_TOKEN_URI",
    "auth_provider_x509_cert_url": "FIREBASE_AUTH_PROVIDER_X509_CERT_URL",
    "client_x509_cert_url": "FIREBASE_CLIENT_X509_CERT_URL",
    "universe_domain": "FIREBASE_UNIVERSE_DOMAIN",
}


def ensure_firebase_app():
    """Retorna/inicializa o app Firebase ou lança erro de configuração."""
    if firebase_admin._apps:
        return firebase_admin.get_app()

    values = {
        credential_key: config(env_name, default="").strip()
        for credential_key, env_name in _ENV_TO_CREDENTIAL.items()
    }
    missing = [env_name for credential_key, env_name in _ENV_TO_CREDENTIAL.items() if not values[credential_key]]
    if missing:
        raise RuntimeError("Configure as variáveis Firebase ausentes: " + ", ".join(missing))

    values["private_key"] = values["private_key"].replace("\\n", "\n")
    cred = credentials.Certificate(values)
    return firebase_admin.initialize_app(cred)
