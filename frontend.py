"""Simple Social — frontend Streamlit pour l'API learningfast."""

import base64
import os
import urllib.parse
from datetime import UTC, datetime

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
MAX_UPLOAD_MB = 50
ALLOWED_EXTENSIONS = ["png", "jpg", "jpeg", "webp", "gif", "mp4", "avi", "mov", "mkv", "webm"]

st.set_page_config(page_title="Simple Social", page_icon="🚀", layout="wide")


class APIError(Exception):
    """Le serveur API est injoignable ou a dépassé le délai."""


# ---------------------------------------------------------------------------
# Session & couche API
# ---------------------------------------------------------------------------


def init_session_state():
    st.session_state.setdefault("token", None)
    st.session_state.setdefault("user", None)


def api(method: str, path: str, **kwargs) -> requests.Response:
    """Appelle l'API avec le header d'auth, gère les erreurs réseau et la session expirée."""
    headers = kwargs.pop("headers", None) or {}
    if st.session_state.token:
        headers.setdefault("Authorization", f"Bearer {st.session_state.token}")
    timeout = kwargs.pop("timeout", 30)
    try:
        response = requests.request(
            method, f"{API_URL}{path}", headers=headers, timeout=timeout, **kwargs
        )
    except requests.exceptions.ConnectionError:
        raise APIError(
            "Impossible de joindre le serveur API — est-il lancé sur le port 8000 ?"
        ) from None
    except requests.exceptions.Timeout:
        raise APIError("Le serveur API met trop de temps à répondre, réessayez.") from None

    if response.status_code == 401 and st.session_state.token:
        # Token expiré ou invalide -> retour à la page de connexion
        st.session_state.token = None
        st.session_state.user = None
        st.toast("Session expirée — reconnectez-vous.")
        st.rerun()
    return response


def error_detail(response: requests.Response, fallback: str = "Une erreur est survenue.") -> str:
    """Extrait un message lisible d'une réponse d'erreur FastAPI (422 inclus)."""
    try:
        data = response.json()
    except ValueError:
        return fallback
    detail = data.get("detail", fallback)
    if isinstance(detail, list):  # erreurs de validation FastAPI
        return "; ".join(
            str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail
        )
    return str(detail) or fallback


# ---------------------------------------------------------------------------
# Transformation d'URL ImageKit
# ---------------------------------------------------------------------------


def encode_text_for_overlay(text: str) -> str:
    """Encode le texte pour l'overlay ImageKit : base64 puis encodage URL."""
    if not text:
        return ""
    base64_text = base64.b64encode(text.encode("utf-8")).decode("utf-8")
    return urllib.parse.quote(base64_text)


def create_transformed_url(original_url: str, transformation_params: str, caption=None) -> str:
    if caption:
        encoded_caption = encode_text_for_overlay(caption)
        # Overlay texte en bas avec fond semi-transparent
        text_overlay = (
            f"l-text,ie-{encoded_caption},ly-N20,lx-20,fs-100,co-white,bg-000000A0,l-end"
        )
        transformation_params = text_overlay

    if not transformation_params:
        return original_url

    parts = original_url.split("/")
    file_path = "/".join(parts[4:])
    base_url = "/".join(parts[:4])
    return f"{base_url}/tr:{transformation_params}/{file_path}"


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------


def login_page():
    st.title("🚀 Simple Social")
    st.caption("Partagez photos et vidéos avec tout le monde.")

    with st.form("auth_form", border=True):
        email = st.text_input("Email", placeholder="vous@exemple.com")
        password = st.text_input("Password", type="password")
        col1, col2 = st.columns(2)
        login_clicked = col1.form_submit_button("Login", type="primary", use_container_width=True)
        signup_clicked = col2.form_submit_button("Sign Up", use_container_width=True)

    if login_clicked:
        handle_login(email, password)
    if signup_clicked:
        handle_signup(email, password)


def handle_login(email: str, password: str):
    if not email or not password:
        st.warning("Entrez votre email et votre mot de passe pour vous connecter.")
        return
    try:
        response = api(
            "post", "/auth/jwt/login", data={"username": email, "password": password}
        )
    except APIError as exc:
        st.error(str(exc))
        return
    if response.status_code == 200:
        st.session_state.token = response.json()["access_token"]
        me = api("get", "/users/me")
        if me.status_code == 200:
            st.session_state.user = me.json()
            st.toast(f"Content de vous revoir, {email} ! 👋")
            st.rerun()
        else:
            st.error(error_detail(me, "Connecté mais impossible de récupérer votre profil."))
    else:
        st.error("Email ou mot de passe incorrect.")


def handle_signup(email: str, password: str):
    if not email or not password:
        st.warning("Entrez votre email et votre mot de passe pour créer un compte.")
        return
    if len(password) < 8:
        st.warning("Le mot de passe doit contenir au moins 8 caractères.")
        return
    try:
        response = api("post", "/auth/register", json={"email": email, "password": password})
    except APIError as exc:
        st.error(str(exc))
        return

    if response.status_code == 201:
        # Connexion automatique du nouvel utilisateur
        login_response = api(
            "post", "/auth/jwt/login", data={"username": email, "password": password}
        )
        if login_response.status_code == 200:
            st.session_state.token = login_response.json()["access_token"]
            me = api("get", "/users/me")
            if me.status_code == 200:
                st.session_state.user = me.json()
                st.toast("Compte créé — bienvenue ! 🎉")
                st.rerun()
                return
        st.success("Compte créé ! Cliquez maintenant sur Login.")
    elif response.status_code == 400:
        st.error("Un compte existe déjà avec cet email — essayez de vous connecter.")
    else:
        st.error(f"Échec de l'inscription : {error_detail(response)}")


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------


def relative_date(iso_date: str) -> str:
    """Convertit une date ISO en libellé relatif lisible."""
    try:
        posted = datetime.fromisoformat(iso_date)
    except ValueError:
        return iso_date[:10]
    days = (datetime.now(UTC).date() - posted.date()).days
    if days <= 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    if days < 7:
        return f"il y a {days} jours"
    return posted.strftime("%d %b %Y")


def delete_post(post_id: str) -> bool:
    try:
        response = api("delete", f"/posts/{post_id}")
    except APIError as exc:
        st.error(str(exc))
        return False
    if response.status_code == 200:
        st.toast("Post supprimé 🗑️")
        return True
    st.error(error_detail(response, "Échec de la suppression du post."))
    return False


def render_delete_controls(post: dict):
    """Bouton supprimer avec confirmation en deux temps."""
    post_id = post["id"]
    if not post.get("is_owner", False):
        return
    confirm_key = f"confirm_delete_{post_id}"
    if st.session_state.get(confirm_key):
        yes, no = st.columns(2)
        if yes.button("Delete", key=f"yes_{post_id}", type="primary", use_container_width=True) and delete_post(post_id):
            st.session_state.pop(confirm_key, None)
            st.rerun()
        if no.button("Cancel", key=f"no_{post_id}", use_container_width=True):
            st.session_state.pop(confirm_key, None)
            st.rerun()
    elif st.button("🗑️", key=f"delete_{post_id}", help="Supprimer ce post"):
        st.session_state[confirm_key] = True
        st.rerun()


def render_post_card(post: dict):
    with st.container(border=True):
        header, _, actions = st.columns([0.85, 0.05, 0.10])
        with header:
            st.markdown(f"**{post.get('email', 'Unknown')}**")
            st.caption(relative_date(post["created_at"]))
        with actions:
            render_delete_controls(post)

        caption = post.get("caption") or ""
        url = post.get("url") or ""
        if not url:
            st.info("Média indisponible.")
            return
        if not url.startswith(("http://", "https://")):
            # URL invalide (anciens posts de test) : on l'affiche sans planter le feed
            st.warning(f"URL de média invalide : {url}")
            if caption:
                st.caption(f"💬 {caption}")
            return
        try:
            if post["file_type"] == "image":
                image_url = create_transformed_url(url, "", caption)
                st.image(image_url, use_container_width=True)
            else:
                video_url = create_transformed_url(
                    url, "w-400,h-200,cm-pad_resize,bg-blurred"
                )
                st.video(video_url)
                if caption:
                    st.caption(f"💬 {caption}")
        except Exception as exc:  # un média corrompu ne doit pas casser le feed
            st.error(f"Impossible d'afficher ce média : {exc}")
            if caption:
                st.caption(f"💬 {caption}")


def feed_page():
    st.title("🏠 Feed")

    try:
        with st.spinner("Chargement du feed…"):
            response = api("get", "/feed")
    except APIError as exc:
        st.error(str(exc))
        return

    if response.status_code != 200:
        st.error(f"Impossible de charger le feed : {error_detail(response)}")
        return

    posts = response.json().get("posts", [])
    if not posts:
        st.info("Aucun post pour l'instant ! Soyez le premier à partager quelque chose. 📸")
        return

    st.caption(f"{len(posts)} post{'s' if len(posts) > 1 else ''}")
    columns = st.columns(2)
    for index, post in enumerate(posts):
        with columns[index % 2]:
            render_post_card(post)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def upload_page():
    st.title("📸 Share Something")

    uploaded_file = st.file_uploader("Choisissez une image ou une vidéo", type=ALLOWED_EXTENSIONS)
    caption = st.text_area("Caption", placeholder="Quoi de neuf ?", max_chars=300)

    if not uploaded_file:
        return

    # Prévisualisation avant publication
    if (uploaded_file.type or "").startswith("video"):
        st.video(uploaded_file.getvalue())
    else:
        st.image(uploaded_file, use_container_width=True)
    st.caption(f"{uploaded_file.name} — {uploaded_file.size / 1_000_000:.1f} Mo")

    if st.button("Share", type="primary", use_container_width=True):
        if uploaded_file.size > MAX_UPLOAD_MB * 1_000_000:
            st.error(
                f"Fichier trop volumineux ({uploaded_file.size / 1_000_000:.1f} Mo) — "
                f"la limite est de {MAX_UPLOAD_MB} Mo."
            )
            return
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
        try:
            response = api(
                "post", "/upload", files=files, data={"caption": caption}, timeout=120
            )
        except APIError as exc:
            st.error(str(exc))
            return
        if response.status_code == 200:
            st.toast("Publié ! 🎉")
            st.session_state.nav = "🏠 Feed"  # redirige vers le feed
            st.rerun()
        else:
            st.error(f"Échec de l'upload : {error_detail(response)}")


# ---------------------------------------------------------------------------
# Application principale
# ---------------------------------------------------------------------------


def main_app():
    with st.sidebar:
        st.markdown(f"### 👋 Hi {st.session_state.user['email'].split('@')[0]}!")
        st.caption(st.session_state.user["email"])
        st.divider()
        page = st.radio("Navigate", ["🏠 Feed", "📸 Upload"], label_visibility="collapsed", key="nav")
        st.divider()
        if st.button("Logout", use_container_width=True):
            st.session_state.token = None
            st.session_state.user = None
            st.toast("À bientôt ! 👋")
            st.rerun()

    if page == "🏠 Feed":
        feed_page()
    else:
        upload_page()


init_session_state()

if st.session_state.user is None:
    login_page()
else:
    main_app()
