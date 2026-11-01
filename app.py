import os
import uuid
import hmac
from pathlib import Path
from datetime import datetime
from functools import wraps

import psycopg
from psycopg.rows import dict_row

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_from_directory,
    abort,
    session,
)

from werkzeug.security import generate_password_hash, check_password_hash


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

ROOT = Path(__file__).resolve().parent

UPLOADS = Path(
    os.environ.get("UPLOADS_DIR", str(ROOT / "uploads"))
)
UPLOADS.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if not DATABASE_URL:
    raise RuntimeError(
        "Falta DATABASE_URL. Configura la cadena de conexión "
        "PostgreSQL de Supabase en las variables de entorno."
    )

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "CAMBIA_ESTA_CLAVE_ANTES_DE_PUBLICAR",
)

app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("RENDER", "").lower() == "true"
)


# ============================================================
# TIPOS DE CONTENIDO Y ARCHIVOS PERMITIDOS
# ============================================================

TYPES = {
    "novela": "Novelas",
    "poema": "Poemas",
    "imagen": "Imágenes",
    "booktrailer": "Booktrailers",
    "playlist": "Playlists",
    "sonido": "Sonidos",
    "video": "Videos",
}

ALLOWED = {
    "pdf", "doc", "docx", "txt", "rtf", "odt",
    "png", "jpg", "jpeg", "gif", "webp",
    "mp3", "wav", "ogg", "m4a",
    "mp4", "webm", "mov",
    "ppt", "pptx", "xls", "xlsx", "csv",
    "m3u", "m3u8", "url",
}


# ============================================================
# CONEXIÓN A SUPABASE POSTGRESQL
# ============================================================

def connect():
    """
    Abre una conexión a PostgreSQL de Supabase.
    Las filas se devuelven como diccionarios.
    """

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada.")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10,
    )


def init_db():
    """
    Comprueba la conexión.
    Las tablas deben existir previamente en Supabase.
    """

    with connect() as conn:
        conn.execute("SELECT 1")


# ============================================================
# ARCHIVOS
# ============================================================

def is_allowed(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED
    )


def save_upload(file):
    if not file or not file.filename:
        return "", ""

    original = os.path.basename(file.filename)

    if not is_allowed(original):
        raise ValueError(
            "Ese tipo de archivo no está permitido."
        )

    stored = f"{uuid.uuid4().hex}_{original}"

    file.save(UPLOADS / stored)

    return stored, original


# ============================================================
# CONTEXTO DE PLANTILLAS
# ============================================================

@app.context_processor
def inject_today():
    return {
        "today": datetime.now().strftime("%Y-%m-%d")
    }


@app.context_processor
def inject_admin_state():
    return {
        "is_admin": bool(session.get("admin_logged_in"))
    }


# ============================================================
# CONFIGURACIÓN DE ADMINISTRACIÓN
# ============================================================

ADMIN_USERNAME = os.environ.get(
    "ADMIN_USERNAME",
    "paguiblue",
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "",
)


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(
                url_for(
                    "admin_login",
                    next=request.path,
                )
            )

        return fn(*args, **kwargs)

    return wrapper


@app.before_request
def protect_editor_actions():
    protected = {
        "nuevo",
        "editar",
        "nuevo_capitulo",
        "capitulo",
        "eliminar_capitulo",
        "eliminar",
        "libro_nuevo",
        "libro_editar",
        "libro_eliminar",
    }

    if (
        request.endpoint in protected
        and not session.get("admin_logged_in")
    ):
        return redirect(
            url_for(
                "admin_login",
                next=request.path,
            )
        )


@app.route("/admin/entrar", methods=["GET", "POST"])
def admin_login():

    if session.get("admin_logged_in"):
        return redirect(url_for("index"))

    if not ADMIN_PASSWORD:
        flash(
            "Configura ADMIN_PASSWORD en las variables "
            "de entorno del servidor.",
            "error",
        )

        return render_template("admin_login.html")

    if request.method == "POST":

        username = request.form.get(
            "username", ""
        ).strip()

        password = request.form.get(
            "password", ""
        )

        if (
            hmac.compare_digest(
                username,
                ADMIN_USERNAME,
            )
            and hmac.compare_digest(
                password,
                ADMIN_PASSWORD,
            )
        ):
            session.pop("diary_unlocked", None)

            session["admin_logged_in"] = True
            session["admin_username"] = ADMIN_USERNAME

            flash(
                "Bienvenida. Ya puedes administrar "
                "tu gabinete literario.",
                "success",
            )

            destination = request.args.get("next", "")

            if (
                not destination.startswith("/")
                or destination.startswith("//")
            ):
                destination = url_for("index")

            return redirect(destination)

        flash(
            "Usuario o contraseña incorrectos.",
            "error",
        )

    return render_template("admin_login.html")


@app.route("/admin/salir", methods=["POST"])
def admin_logout():

    session.pop("admin_logged_in", None)
    session.pop("admin_username", None)

    flash(
        "Sesión de administración cerrada.",
        "success",
    )

    return redirect(url_for("index"))


# ============================================================
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/")
def index():

    kind = request.args.get("tipo", "")
    search = request.args.get("buscar", "").strip()

    sql = "SELECT * FROM items WHERE 1=1"
    args = []

    if kind in TYPES:
        sql += " AND kind = %s"
        args.append(kind)

    if search:
        sql += """
            AND (
                title ILIKE %s
                OR description ILIKE %s
                OR body ILIKE %s
                OR original_name ILIKE %s
            )
        """

        args.extend([f"%{search}%"] * 4)

    sql += " ORDER BY id DESC"

    with connect() as conn:

        items = conn.execute(
            sql,
            args,
        ).fetchall()

        count_rows = conn.execute(
            """
            SELECT kind, COUNT(*) AS n
            FROM items
            GROUP BY kind
            """
        ).fetchall()

        counts = {
            row["kind"]: row["n"]
            for row in count_rows
        }

        chapter_rows = conn.execute(
            """
            SELECT novel_id, COUNT(*) AS n
            FROM chapters
            GROUP BY novel_id
            """
        ).fetchall()

        chapters_count = {
            row["novel_id"]: row["n"]
            for row in chapter_rows
        }

    return render_template(
        "index.html",
        items=items,
        types=TYPES,
        selected=kind,
        search=search,
        counts=counts,
        chapters_count=chapters_count,
    )


# ============================================================
# CREAR MATERIAL LITERARIO
# ============================================================

@app.route("/nuevo", methods=["GET", "POST"])
def nuevo():

    if request.method == "POST":

        title = request.form.get(
            "title", ""
        ).strip()

        kind = request.form.get(
            "kind", "poema"
        )

        description = request.form.get(
            "description", ""
        ).strip()

        body = request.form.get(
            "body", ""
        ).strip()

        external_url = request.form.get(
            "external_url", ""
        ).strip()

        file = request.files.get("file")

        if not title or kind not in TYPES:
            flash(
                "Escribe un título y selecciona una sección.",
                "error",
            )

            return redirect(url_for("nuevo"))

        try:
            stored, original = save_upload(file)

        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("nuevo"))

        if (
            kind in {"novela", "poema"}
            and not body
            and not stored
        ):
            flash(
                "Escribe el contenido o adjunta un archivo.",
                "error",
            )

            return redirect(url_for("nuevo"))

        if (
            kind in {
                "imagen",
                "booktrailer",
                "sonido",
                "video",
            }
            and not stored
            and not external_url
        ):
            flash(
                "Adjunta un archivo o pega un enlace multimedia.",
                "error",
            )

            return redirect(url_for("nuevo"))

        if (
            kind == "playlist"
            and not stored
            and not external_url
            and not body
        ):
            flash(
                "Agrega un enlace, un archivo de playlist "
                "o una descripción.",
                "error",
            )

            return redirect(url_for("nuevo"))

        if (
            stored
            and original.lower().endswith(".txt")
            and kind in {"novela", "poema"}
            and not body
        ):
            try:
                body = (
                    UPLOADS / stored
                ).read_text(encoding="utf-8")

            except UnicodeDecodeError:
                pass

        with connect() as conn:

            row = conn.execute(
                """
                INSERT INTO items (
                    title,
                    kind,
                    description,
                    body,
                    stored_name,
                    original_name,
                    external_url,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    title,
                    kind,
                    description,
                    body,
                    stored,
                    original,
                    external_url,
                    datetime.now().strftime(
                        "%d/%m/%Y %H:%M"
                    ),
                ),
            ).fetchone()

            new_id = row["id"]

        flash(
            "¡Guardado en tu gabinete!",
            "success",
        )

        return redirect(
            url_for("detalle", item_id=new_id)
        )

    return render_template(
        "form.html",
        types=TYPES,
        item=None,
    )


# ============================================================
# DETALLE DE MATERIAL
# ============================================================

@app.route("/item/<int:item_id>")
def detalle(item_id):

    with connect() as conn:

        item = conn.execute(
            "SELECT * FROM items WHERE id = %s",
            (item_id,),
        ).fetchone()

        if not item:
            abort(404)

        chapters = []

        if item["kind"] == "novela":
            chapters = conn.execute(
                """
                SELECT * FROM chapters
                WHERE novel_id = %s
                ORDER BY chapter_number
                """,
                (item_id,),
            ).fetchall()

    return render_template(
        "detail.html",
        item=item,
        chapters=chapters,
        types=TYPES,
    )


# ============================================================
# EDITAR MATERIAL
# ============================================================

@app.route(
    "/item/<int:item_id>/editar",
    methods=["GET", "POST"],
)
def editar(item_id):

    with connect() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE id = %s",
            (item_id,),
        ).fetchone()

    if not item:
        abort(404)

    if request.method == "POST":

        title = request.form.get(
            "title", ""
        ).strip()

        if not title:
            flash(
                "El título es obligatorio.",
                "error",
            )

            return redirect(
                url_for("editar", item_id=item_id)
            )

        with connect() as conn:
            conn.execute(
                """
                UPDATE items
                SET title = %s,
                    description = %s,
                    body = %s,
                    external_url = %s
                WHERE id = %s
                """,
                (
                    title,
                    request.form.get(
                        "description", ""
                    ).strip(),
                    request.form.get(
                        "body", ""
                    ).strip(),
                    request.form.get(
                        "external_url", ""
                    ).strip(),
                    item_id,
                ),
            )

        flash(
            "Cambios guardados.",
            "success",
        )

        return redirect(
            url_for("detalle", item_id=item_id)
        )

    return render_template(
        "form.html",
        types=TYPES,
        item=item,
    )


# ============================================================
# CAPÍTULOS
# ============================================================

@app.route(
    "/novela/<int:novel_id>/capitulo/nuevo",
    methods=["GET", "POST"],
)
def nuevo_capitulo(novel_id):

    with connect() as conn:

        novel = conn.execute(
            """
            SELECT * FROM items
            WHERE id = %s AND kind = 'novela'
            """,
            (novel_id,),
        ).fetchone()

        if not novel:
            abort(404)

        row = conn.execute(
            """
            SELECT COALESCE(MAX(chapter_number), 0) + 1 AS n
            FROM chapters
            WHERE novel_id = %s
            """,
            (novel_id,),
        ).fetchone()

        next_number = row["n"]

    if request.method == "POST":

        title = request.form.get(
            "title", ""
        ).strip()

        body = request.form.get(
            "body", ""
        ).strip()

        if not title or not body:
            flash(
                "El capítulo necesita título y contenido.",
                "error",
            )

            return redirect(
                url_for(
                    "nuevo_capitulo",
                    novel_id=novel_id,
                )
            )

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO chapters (
                    novel_id,
                    title,
                    body,
                    chapter_number,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    novel_id,
                    title,
                    body,
                    next_number,
                    datetime.now().strftime(
                        "%d/%m/%Y %H:%M"
                    ),
                ),
            )

        flash(
            "Capítulo guardado.",
            "success",
        )

        return redirect(
            url_for("detalle", item_id=novel_id)
        )

    return render_template(
        "chapter_form.html",
        novel=novel,
        chapter=None,
        number=next_number,
    )


@app.route(
    "/capitulo/<int:chapter_id>",
    methods=["GET", "POST"],
)
def capitulo(chapter_id):

    with connect() as conn:
        chapter = conn.execute(
            "SELECT * FROM chapters WHERE id = %s",
            (chapter_id,),
        ).fetchone()

    if not chapter:
        abort(404)

    if request.method == "POST":

        title = request.form.get(
            "title", ""
        ).strip()

        body = request.form.get(
            "body", ""
        ).strip()

        if not title or not body:
            flash(
                "Completa el título y el contenido.",
                "error",
            )

            return redirect(
                url_for(
                    "capitulo",
                    chapter_id=chapter_id,
                )
            )

        with connect() as conn:
            conn.execute(
                """
                UPDATE chapters
                SET title = %s, body = %s
                WHERE id = %s
                """,
                (title, body, chapter_id),
            )

        flash(
            "Capítulo actualizado.",
            "success",
        )

        return redirect(
            url_for(
                "detalle",
                item_id=chapter["novel_id"],
            )
        )

    return render_template(
        "chapter_form.html",
        novel_id=chapter["novel_id"],
        chapter=chapter,
        number=chapter["chapter_number"],
    )


@app.route(
    "/capitulo/<int:chapter_id>/eliminar",
    methods=["POST"],
)
def eliminar_capitulo(chapter_id):

    with connect() as conn:

        chapter = conn.execute(
            "SELECT * FROM chapters WHERE id = %s",
            (chapter_id,),
        ).fetchone()

        if not chapter:
            abort(404)

        novel_id = chapter["novel_id"]

        conn.execute(
            "DELETE FROM chapters WHERE id = %s",
            (chapter_id,),
        )

    flash(
        "Capítulo eliminado.",
        "success",
    )

    return redirect(
        url_for("detalle", item_id=novel_id)
    )


@app.route(
    "/item/<int:item_id>/eliminar",
    methods=["POST"],
)
def eliminar(item_id):

    with connect() as conn:

        item = conn.execute(
            "SELECT * FROM items WHERE id = %s",
            (item_id,),
        ).fetchone()

        if item:

            if (
                item["stored_name"]
                and (UPLOADS / item["stored_name"]).exists()
            ):
                (UPLOADS / item["stored_name"]).unlink()

            conn.execute(
                "DELETE FROM chapters WHERE novel_id = %s",
                (item_id,),
            )

            conn.execute(
                "DELETE FROM items WHERE id = %s",
                (item_id,),
            )

    flash(
        "Material eliminado.",
        "success",
    )

    return redirect(url_for("index"))


# ============================================================
# BIBLIOTECA PERSONAL
# ============================================================

BOOK_FIELDS = [
    ("title", "Título"),
    ("author", "Autor o autora"),
    ("rating", "Calificación"),
    ("shelf_code", "Código en estantería"),
    ("edition", "Edición"),
    ("publisher", "Editorial"),
    ("status", "Estado de lectura"),
    ("tags", "Etiquetas"),
    ("start_date", "Fecha de inicio"),
    ("finish_date", "Fecha de término"),
    ("publication_date", "Fecha de publicación"),
    ("format", "Formato"),
    ("genre", "Género"),
    ("isbn", "ISBN"),
    ("saga_type", "Tipo de saga"),
    ("saga_title", "Nombre de saga"),
    ("chapter_count", "Número de capítulos"),
    ("pages", "Número de páginas"),
    ("price", "Precio"),
    ("subgenre", "Subgénero"),
    ("themes", "Temas principales"),
    ("review", "Reseña"),
    ("synopsis", "Sinopsis"),
    ("opinion", "Opinión personal"),
    ("favorite_quotes", "Frases favoritas"),
    ("playlist", "Playlist del libro"),
    ("characters", "Personajes"),
    ("moments", "Momentos favoritos"),
]


@app.route("/biblioteca")
def biblioteca():

    query = request.args.get(
        "buscar", ""
    ).strip()

    status = request.args.get(
        "estado", ""
    )

    sql = "SELECT * FROM books WHERE 1=1"
    args = []

    if query:
        sql += """
            AND (
                title ILIKE %s
                OR author ILIKE %s
                OR genre ILIKE %s
                OR tags ILIKE %s
                OR isbn ILIKE %s
            )
        """

        args.extend([f"%{query}%"] * 5)

    if status:
        sql += " AND status = %s"
        args.append(status)

    sql += " ORDER BY title"

    with connect() as conn:

        books = conn.execute(
            sql,
            args,
        ).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) AS n FROM books"
        ).fetchone()["n"]

        read = conn.execute(
            "SELECT COUNT(*) AS n FROM books WHERE status = 'Leído'"
        ).fetchone()["n"]

        reading = conn.execute(
            "SELECT COUNT(*) AS n FROM books WHERE status = 'Leyendo'"
        ).fetchone()["n"]

    return render_template(
        "library.html",
        books=books,
        q=query,
        status=status,
        total=total,
        read=read,
        reading=reading,
    )


@app.route(
    "/biblioteca/nuevo",
    methods=["GET", "POST"],
)
def libro_nuevo():

    if request.method == "POST":

        data = {
            field: request.form.get(
                field, ""
            ).strip()
            for field, label in BOOK_FIELDS
        }

        if not data["title"]:
            flash(
                "El título del libro es obligatorio.",
                "error",
            )

            return redirect(url_for("libro_nuevo"))

        try:
            cover_name, cover_original = save_upload(
                request.files.get("cover")
            )

        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("libro_nuevo"))

        data["cover_name"] = cover_name
        data["cover_original"] = cover_original
        data["created_at"] = datetime.now().strftime(
            "%d/%m/%Y %H:%M"
        )

        columns = list(data.keys())
        placeholders = ",".join(
            ["%s"] * len(columns)
        )

        with connect() as conn:

            row = conn.execute(
                f"""
                INSERT INTO books ({','.join(columns)})
                VALUES ({placeholders})
                RETURNING id
                """,
                [data[column] for column in columns],
            ).fetchone()

            book_id = row["id"]

        flash(
            "Libro agregado a tu biblioteca.",
            "success",
        )

        return redirect(
            url_for(
                "libro_detalle",
                book_id=book_id,
            )
        )

    return render_template(
        "book_form.html",
        book=None,
        fields=BOOK_FIELDS,
    )


@app.route("/biblioteca/<int:book_id>")
def libro_detalle(book_id):

    with connect() as conn:
        book = conn.execute(
            "SELECT * FROM books WHERE id = %s",
            (book_id,),
        ).fetchone()

    if not book:
        abort(404)

    return render_template(
        "book_detail.html",
        book=book,
        fields=BOOK_FIELDS,
    )


@app.route(
    "/biblioteca/<int:book_id>/editar",
    methods=["GET", "POST"],
)
def libro_editar(book_id):

    with connect() as conn:
        book = conn.execute(
            "SELECT * FROM books WHERE id = %s",
            (book_id,),
        ).fetchone()

    if not book:
        abort(404)

    if request.method == "POST":

        data = {
            field: request.form.get(
                field, ""
            ).strip()
            for field, label in BOOK_FIELDS
        }

        if not data["title"]:
            flash(
                "El título del libro es obligatorio.",
                "error",
            )

            return redirect(
                url_for(
                    "libro_editar",
                    book_id=book_id,
                )
            )

        with connect() as conn:
            conn.execute(
                "UPDATE books SET "
                + ",".join(
                    f"{key} = %s"
                    for key in data
                )
                + " WHERE id = %s",
                list(data.values()) + [book_id],
            )

        flash(
            "Ficha del libro actualizada.",
            "success",
        )

        return redirect(
            url_for(
                "libro_detalle",
                book_id=book_id,
            )
        )

    return render_template(
        "book_form.html",
        book=book,
        fields=BOOK_FIELDS,
    )


@app.route(
    "/biblioteca/<int:book_id>/eliminar",
    methods=["POST"],
)
def libro_eliminar(book_id):

    with connect() as conn:

        book = conn.execute(
            "SELECT * FROM books WHERE id = %s",
            (book_id,),
        ).fetchone()

        if book:

            if (
                book["cover_name"]
                and (UPLOADS / book["cover_name"]).exists()
            ):
                (UPLOADS / book["cover_name"]).unlink()

            conn.execute(
                "DELETE FROM books WHERE id = %s",
                (book_id,),
            )

    flash(
        "Libro eliminado de tu biblioteca.",
        "success",
    )

    return redirect(url_for("biblioteca"))


# ============================================================
# DIARIO PRIVADO
# ============================================================

def diary_is_setup():

    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM diary_auth WHERE id = 1"
        ).fetchone() is not None


def diary_required(fn):

    @wraps(fn)
    def wrapper(*args, **kwargs):

        if not session.get("diary_unlocked"):
            return redirect(
                url_for(
                    "diario_login",
                    next=request.path,
                )
            )

        return fn(*args, **kwargs)

    return wrapper


@app.route(
    "/diario/configurar",
    methods=["GET", "POST"],
)
def diario_configurar():

    if diary_is_setup():
        return redirect(url_for("diario_login"))

    if request.method == "POST":

        password1 = request.form.get(
            "password", ""
        )

        password2 = request.form.get(
            "confirm_password", ""
        )

        if len(password1) < 10:
            flash(
                "Usa una contraseña de al menos 10 caracteres.",
                "error",
            )

        elif password1 != password2:
            flash(
                "Las contraseñas no coinciden.",
                "error",
            )

        else:

            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO diary_auth(id, password_hash)
                    VALUES (1, %s)
                    """,
                    (generate_password_hash(password1),),
                )

            session["diary_unlocked"] = True

            flash(
                "Tu diario quedó configurado. Recuerda tu contraseña.",
                "success",
            )

            return redirect(url_for("diario"))

    return render_template("diary_setup.html")


@app.route(
    "/diario/entrar",
    methods=["GET", "POST"],
)
def diario_login():

    if not diary_is_setup():
        return redirect(url_for("diario_configurar"))

    if session.get("diary_unlocked"):
        return redirect(url_for("diario"))

    if request.method == "POST":

        password = request.form.get(
            "password", ""
        )

        with connect() as conn:
            row = conn.execute(
                "SELECT password_hash FROM diary_auth WHERE id = 1"
            ).fetchone()

        if row and check_password_hash(
            row["password_hash"],
            password,
        ):

            session.pop("admin_logged_in", None)
            session.pop("admin_username", None)
            session["diary_unlocked"] = True

            flash(
                "Diario desbloqueado.",
                "success",
            )

            return redirect(url_for("diario"))

        flash(
            "Contraseña incorrecta.",
            "error",
        )

    return render_template("diary_login.html")


@app.route("/diario/salir", methods=["POST"])
def diario_salir():

    session.pop("diary_unlocked", None)

    flash(
        "Cerraste el diario.",
        "success",
    )

    return redirect(url_for("index"))


@app.route("/diario")
@diary_required
def diario():

    query = request.args.get(
        "buscar", ""
    ).strip()

    sql = "SELECT * FROM diary_entries"
    args = []

    if query:
        sql += """
            WHERE title ILIKE %s
               OR content ILIKE %s
               OR mood ILIKE %s
        """

        args = [f"%{query}%"] * 3

    sql += " ORDER BY entry_date DESC, id DESC"

    with connect() as conn:
        entries = conn.execute(
            sql,
            args,
        ).fetchall()

    return render_template(
        "diary.html",
        entries=entries,
        q=query,
    )


@app.route(
    "/diario/nueva",
    methods=["GET", "POST"],
)
@diary_required
def diario_nueva():

    if request.method == "POST":

        title = request.form.get(
            "title", ""
        ).strip()

        content = request.form.get(
            "content", ""
        ).strip()

        mood = request.form.get(
            "mood", ""
        ).strip()

        entry_date = request.form.get(
            "entry_date", ""
        ) or datetime.now().strftime("%Y-%m-%d")

        if not title or not content:
            flash(
                "Escribe un título y el contenido de la entrada.",
                "error",
            )

            return redirect(url_for("diario_nueva"))

        with connect() as conn:

            row = conn.execute(
                """
                INSERT INTO diary_entries(
                    title, mood, entry_date, content
                )
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (
                    title,
                    mood,
                    entry_date,
                    content,
                ),
            ).fetchone()

            entry_id = row["id"]

        flash(
            "Tu entrada quedó guardada en el diario privado.",
            "success",
        )

        return redirect(
            url_for(
                "diario_editar",
                entry_id=entry_id,
            )
        )

    return render_template(
        "diary_form.html",
        entry=None,
    )


@app.route(
    "/diario/entrada/<int:entry_id>",
    methods=["GET", "POST"],
)
@diary_required
def diario_editar(entry_id):

    with connect() as conn:
        entry = conn.execute(
            "SELECT * FROM diary_entries WHERE id = %s",
            (entry_id,),
        ).fetchone()

    if not entry:
        abort(404)

    if request.method == "POST":

        title = request.form.get(
            "title", ""
        ).strip()

        content = request.form.get(
            "content", ""
        ).strip()

        mood = request.form.get(
            "mood", ""
        ).strip()

        entry_date = request.form.get(
            "entry_date", ""
        ) or datetime.now().strftime("%Y-%m-%d")

        if not title or not content:
            flash(
                "El título y el contenido son obligatorios.",
                "error",
            )

            return redirect(
                url_for(
                    "diario_editar",
                    entry_id=entry_id,
                )
            )

        with connect() as conn:
            conn.execute(
                """
                UPDATE diary_entries
                SET title = %s,
                    content = %s,
                    mood = %s,
                    entry_date = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    title,
                    content,
                    mood,
                    entry_date,
                    entry_id,
                ),
            )

        flash(
            "Entrada actualizada.",
            "success",
        )

        return redirect(url_for("diario"))

    return render_template(
        "diary_form.html",
        entry=entry,
    )


@app.route(
    "/diario/entrada/<int:entry_id>/eliminar",
    methods=["POST"],
)
@diary_required
def diario_eliminar(entry_id):

    with connect() as conn:
        conn.execute(
            "DELETE FROM diary_entries WHERE id = %s",
            (entry_id,),
        )

    flash(
        "Entrada eliminada.",
        "success",
    )

    return redirect(url_for("diario"))


# ============================================================
# ARCHIVOS SUBIDOS
# ============================================================

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(
        UPLOADS,
        filename,
    )


@app.route("/item/<int:item_id>/descargar")
def descargar(item_id):

    with connect() as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE id = %s",
            (item_id,),
        ).fetchone()

    if not item or not item["stored_name"]:
        abort(404)

    return send_from_directory(
        UPLOADS,
        item["stored_name"],
        as_attachment=True,
        download_name=item["original_name"],
    )


# ============================================================
# ERRORES
# ============================================================

@app.errorhandler(413)
def too_large(_error):

    flash(
        "El archivo supera el límite de 100 MB.",
        "error",
    )

    return redirect(
        request.referrer or url_for("index")
    )


# ============================================================
# INICIO
# ============================================================

init_db()

if __name__ == "__main__":
    app.run(
        debug=os.environ.get(
            "FLASK_DEBUG", ""
        ).lower() == "true"
    )
