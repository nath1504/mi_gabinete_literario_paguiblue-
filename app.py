import os, sqlite3, uuid
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort, session
from werkzeug.security import generate_password_hash, check_password_hash

ROOT = Path(__file__).resolve().parent
UPLOADS = Path(os.environ.get("UPLOADS_DIR", str(ROOT / "uploads")))
UPLOADS.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("DATABASE_PATH", str(ROOT / "gabinete.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CAMBIA_ESTA_CLAVE_SECRETA_ANTES_DE_PUBLICAR")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("RENDER", "").lower() == "true"

TYPES = {
    "novela": "Novelas", "poema": "Poemas", "imagen": "Imágenes",
    "booktrailer": "Booktrailers", "playlist": "Playlists",
    "sonido": "Sonidos", "video": "Videos"
}
ALLOWED = {"pdf","doc","docx","txt","rtf","odt","png","jpg","jpeg","gif","webp",
           "mp3","wav","ogg","m4a","mp4","webm","mov","ppt","pptx","xls","xlsx","csv",
           "m3u","m3u8","url"}

def connect():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c

def init_db():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, kind TEXT NOT NULL,
          description TEXT DEFAULT '', body TEXT DEFAULT '', stored_name TEXT DEFAULT '',
          original_name TEXT DEFAULT '', external_url TEXT DEFAULT '', created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS chapters (
          id INTEGER PRIMARY KEY AUTOINCREMENT, novel_id INTEGER NOT NULL, title TEXT NOT NULL,
          body TEXT DEFAULT '', chapter_number INTEGER NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(novel_id) REFERENCES items(id) ON DELETE CASCADE)""")
        c.execute("""CREATE TABLE IF NOT EXISTS books (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, author TEXT DEFAULT '',
          rating TEXT DEFAULT '', shelf_code TEXT DEFAULT '', edition TEXT DEFAULT '',
          publisher TEXT DEFAULT '', status TEXT DEFAULT 'Pendiente', tags TEXT DEFAULT '',
          start_date TEXT DEFAULT '', finish_date TEXT DEFAULT '', publication_date TEXT DEFAULT '',
          format TEXT DEFAULT 'Físico', genre TEXT DEFAULT '', isbn TEXT DEFAULT '',
          saga_type TEXT DEFAULT 'Libro único', saga_title TEXT DEFAULT '',
          chapter_count TEXT DEFAULT '', pages TEXT DEFAULT '', price TEXT DEFAULT '',
          subgenre TEXT DEFAULT '', themes TEXT DEFAULT '', review TEXT DEFAULT '',
          synopsis TEXT DEFAULT '', opinion TEXT DEFAULT '', favorite_quotes TEXT DEFAULT '',
          playlist TEXT DEFAULT '', characters TEXT DEFAULT '', moments TEXT DEFAULT '',
          cover_name TEXT DEFAULT '', cover_original TEXT DEFAULT '', created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS admin_auth (
          id INTEGER PRIMARY KEY CHECK (id=1), username TEXT NOT NULL, password_hash TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS diary_auth (
          id INTEGER PRIMARY KEY CHECK (id=1), password_hash TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS diary_entries (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, mood TEXT DEFAULT '',
          entry_date TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL)""")

def is_allowed(filename):
    return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED

def save_upload(f):
    if not f or not f.filename:
        return "", ""
    original = os.path.basename(f.filename)
    if not is_allowed(original):
        raise ValueError("Ese tipo de archivo no está permitido.")
    stored = uuid.uuid4().hex + "_" + original
    f.save(UPLOADS / stored)
    return stored, original

@app.context_processor
def inject_today():
    return {"today": datetime.now().strftime("%Y-%m-%d")}

def diary_is_setup():
    with connect() as c:
        return c.execute("SELECT 1 FROM diary_auth WHERE id=1").fetchone() is not None

def diary_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("diary_unlocked"):
            return redirect(url_for("diario_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "paguiblue")
# Change ADMIN_PASSWORD in the hosting provider's environment variables before publishing.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "P4gu!Blue_31Oct#26")

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

@app.before_request
def protect_editor_actions():
    protected = {
        "nuevo", "editar", "nuevo_capitulo", "capitulo",
        "eliminar_capitulo", "eliminar", "libro_nuevo",
        "libro_editar", "libro_eliminar"
    }
    if request.endpoint in protected and not session.get("admin_logged_in"):
        return redirect(url_for("admin_login", next=request.path))

@app.context_processor
def inject_admin_state():
    return {"is_admin": bool(session.get("admin_logged_in"))}

@app.route("/admin/entrar", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and check_password_hash(generate_password_hash(ADMIN_PASSWORD), password):
            session.clear()
            session["admin_logged_in"] = True
            session["admin_username"] = ADMIN_USERNAME
            flash("Bienvenida, paguiblue. Ya puedes administrar tu gabinete.", "success")
            destination = request.args.get("next", "")
            if not destination.startswith("/") or destination.startswith("//"):
                destination = url_for("index")
            return redirect(destination)
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("admin_login.html")

@app.route("/admin/salir", methods=["POST"])
def admin_logout():
    session.clear()
    flash("Sesión de administración cerrada.", "success")
    return redirect(url_for("index"))

@app.route("/")
def index():
    kind=request.args.get("tipo","")
    search=request.args.get("buscar","").strip()
    sql="SELECT * FROM items WHERE 1=1"; args=[]
    if kind in TYPES:
        sql+=" AND kind=?"; args.append(kind)
    if search:
        sql+=" AND (title LIKE ? OR description LIKE ? OR body LIKE ? OR original_name LIKE ?)"
        args += [f"%{search}%"]*4
    sql+=" ORDER BY id DESC"
    with connect() as c:
        items=c.execute(sql,args).fetchall()
        counts={r["kind"]:r["n"] for r in c.execute("SELECT kind,COUNT(*) n FROM items GROUP BY kind")}
        chapters_count={r["novel_id"]:r["n"] for r in c.execute("SELECT novel_id,COUNT(*) n FROM chapters GROUP BY novel_id")}
    return render_template("index.html",items=items,types=TYPES,selected=kind,search=search,counts=counts,chapters_count=chapters_count)

@app.route("/nuevo",methods=["GET","POST"])
def nuevo():
    if request.method=="POST":
        title=request.form.get("title","").strip()
        kind=request.form.get("kind","poema")
        description=request.form.get("description","").strip()
        body=request.form.get("body","").strip()
        external_url=request.form.get("external_url","").strip()
        f=request.files.get("file")
        if not title or kind not in TYPES:
            flash("Escribe un título y selecciona una sección.","error"); return redirect(url_for("nuevo"))
        try: stored, original = save_upload(f)
        except ValueError as e:
            flash(str(e),"error"); return redirect(url_for("nuevo"))
        if kind in {"novela","poema"} and not body and not stored:
            flash("Escribe el contenido o adjunta un archivo.","error"); return redirect(url_for("nuevo"))
        if kind in {"imagen","booktrailer","sonido","video"} and not stored and not external_url:
            flash("Adjunta un archivo o pega un enlace multimedia.","error"); return redirect(url_for("nuevo"))
        if kind=="playlist" and not stored and not external_url and not body:
            flash("Agrega un enlace, un archivo de playlist o una descripción.","error"); return redirect(url_for("nuevo"))
        if stored and original.lower().endswith(".txt") and kind in {"novela","poema"} and not body:
            try: body=(UPLOADS/stored).read_text(encoding="utf-8")
            except UnicodeDecodeError: pass
        with connect() as c:
            c.execute("""INSERT INTO items(title,kind,description,body,stored_name,original_name,external_url,created_at)
              VALUES(?,?,?,?,?,?,?,?)""",(title,kind,description,body,stored,original,external_url,datetime.now().strftime("%d/%m/%Y %H:%M")))
            new_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        flash("¡Guardado en tu gabinete!","success")
        return redirect(url_for("detalle",item_id=new_id))
    return render_template("form.html",types=TYPES,item=None)

@app.route("/item/<int:item_id>")
def detalle(item_id):
    with connect() as c:
        item=c.execute("SELECT * FROM items WHERE id=?",(item_id,)).fetchone()
        if not item: abort(404)
        chapters=c.execute("SELECT * FROM chapters WHERE novel_id=? ORDER BY chapter_number",(item_id,)).fetchall() if item["kind"]=="novela" else []
    return render_template("detail.html",item=item,chapters=chapters,types=TYPES)

@app.route("/item/<int:item_id>/editar",methods=["GET","POST"])
def editar(item_id):
    with connect() as c: item=c.execute("SELECT * FROM items WHERE id=?",(item_id,)).fetchone()
    if not item: abort(404)
    if request.method=="POST":
        title=request.form.get("title","").strip()
        if not title:
            flash("El título es obligatorio.","error"); return redirect(url_for("editar",item_id=item_id))
        with connect() as c:
            c.execute("UPDATE items SET title=?,description=?,body=?,external_url=? WHERE id=?",
              (title,request.form.get("description","").strip(),request.form.get("body","").strip(),request.form.get("external_url","").strip(),item_id))
        flash("Cambios guardados.","success"); return redirect(url_for("detalle",item_id=item_id))
    return render_template("form.html",types=TYPES,item=item)

@app.route("/novela/<int:novel_id>/capitulo/nuevo",methods=["GET","POST"])
def nuevo_capitulo(novel_id):
    with connect() as c:
        novel=c.execute("SELECT * FROM items WHERE id=? AND kind='novela'",(novel_id,)).fetchone()
        if not novel: abort(404)
        next_number=c.execute("SELECT COALESCE(MAX(chapter_number),0)+1 n FROM chapters WHERE novel_id=?",(novel_id,)).fetchone()["n"]
    if request.method=="POST":
        title=request.form.get("title","").strip(); body=request.form.get("body","").strip()
        if not title or not body:
            flash("El capítulo necesita título y contenido.","error"); return redirect(url_for("nuevo_capitulo",novel_id=novel_id))
        with connect() as c:
            c.execute("INSERT INTO chapters(novel_id,title,body,chapter_number,created_at) VALUES(?,?,?,?,?)",
              (novel_id,title,body,next_number,datetime.now().strftime("%d/%m/%Y %H:%M")))
        flash("Capítulo guardado.","success"); return redirect(url_for("detalle",item_id=novel_id))
    return render_template("chapter_form.html",novel=novel,chapter=None,number=next_number)

@app.route("/capitulo/<int:chapter_id>",methods=["GET","POST"])
def capitulo(chapter_id):
    with connect() as c: ch=c.execute("SELECT * FROM chapters WHERE id=?",(chapter_id,)).fetchone()
    if not ch: abort(404)
    if request.method=="POST":
        title=request.form.get("title","").strip(); body=request.form.get("body","").strip()
        if not title or not body:
            flash("Completa el título y el contenido.","error"); return redirect(url_for("capitulo",chapter_id=chapter_id))
        with connect() as c: c.execute("UPDATE chapters SET title=?,body=? WHERE id=?",(title,body,chapter_id))
        flash("Capítulo actualizado.","success"); return redirect(url_for("detalle",item_id=ch["novel_id"]))
    return render_template("chapter_form.html",novel_id=ch["novel_id"],chapter=ch,number=ch["chapter_number"])

@app.route("/capitulo/<int:chapter_id>/eliminar",methods=["POST"])
def eliminar_capitulo(chapter_id):
    with connect() as c:
        ch=c.execute("SELECT * FROM chapters WHERE id=?",(chapter_id,)).fetchone()
        if ch:
            novel_id=ch["novel_id"]; c.execute("DELETE FROM chapters WHERE id=?",(chapter_id,))
        else: abort(404)
    flash("Capítulo eliminado.","success"); return redirect(url_for("detalle",item_id=novel_id))

@app.route("/item/<int:item_id>/eliminar",methods=["POST"])
def eliminar(item_id):
    with connect() as c:
        item=c.execute("SELECT * FROM items WHERE id=?",(item_id,)).fetchone()
        if item:
            if item["stored_name"] and (UPLOADS/item["stored_name"]).exists(): (UPLOADS/item["stored_name"]).unlink()
            c.execute("DELETE FROM chapters WHERE novel_id=?",(item_id,))
            c.execute("DELETE FROM items WHERE id=?",(item_id,))
    flash("Material eliminado.","success"); return redirect(url_for("index"))

# Personal library / book catalog
BOOK_FIELDS = [
    ("title","Título"),("author","Autor o autora"),("rating","Calificación"),("shelf_code","Código en estantería"),
    ("edition","Edición"),("publisher","Editorial"),("status","Estado de lectura"),("tags","Etiquetas"),
    ("start_date","Fecha de inicio"),("finish_date","Fecha de término"),("publication_date","Fecha de publicación"),
    ("format","Formato"),("genre","Género"),("isbn","ISBN"),("saga_type","Tipo de saga"),
    ("saga_title","Nombre de saga"),("chapter_count","Número de capítulos"),("pages","Número de páginas"),
    ("price","Precio"),("subgenre","Subgénero"),("themes","Temas principales"),("review","Reseña"),
    ("synopsis","Sinopsis"),("opinion","Opinión personal"),("favorite_quotes","Frases favoritas"),
    ("playlist","Playlist del libro"),("characters","Personajes"),("moments","Momentos favoritos")
]
@app.route("/biblioteca")
def biblioteca():
    q=request.args.get("buscar","").strip()
    status=request.args.get("estado","")
    sql="SELECT * FROM books WHERE 1=1"; args=[]
    if q:
        sql+=" AND (title LIKE ? OR author LIKE ? OR genre LIKE ? OR tags LIKE ? OR isbn LIKE ?)"
        args += [f"%{q}%"]*5
    if status:
        sql+=" AND status=?"; args.append(status)
    sql+=" ORDER BY title COLLATE NOCASE"
    with connect() as c:
        books=c.execute(sql,args).fetchall()
        total=c.execute("SELECT COUNT(*) n FROM books").fetchone()["n"]
        read=c.execute("SELECT COUNT(*) n FROM books WHERE status='Leído'").fetchone()["n"]
        reading=c.execute("SELECT COUNT(*) n FROM books WHERE status='Leyendo'").fetchone()["n"]
    return render_template("library.html",books=books,q=q,status=status,total=total,read=read,reading=reading)

@app.route("/biblioteca/nuevo",methods=["GET","POST"])
def libro_nuevo():
    if request.method=="POST":
        data={field:request.form.get(field,"").strip() for field,label in BOOK_FIELDS}
        if not data["title"]:
            flash("El título del libro es obligatorio.","error")
            return redirect(url_for("libro_nuevo"))
        cover_name,cover_original="", ""
        try:
            cover_name,cover_original=save_upload(request.files.get("cover"))
        except ValueError as e:
            flash(str(e),"error"); return redirect(url_for("libro_nuevo"))
        data["cover_name"]=cover_name; data["cover_original"]=cover_original
        data["created_at"]=datetime.now().strftime("%d/%m/%Y %H:%M")
        columns=list(data.keys())
        with connect() as c:
            c.execute(f"INSERT INTO books({','.join(columns)}) VALUES({','.join(['?']*len(columns))})",[data[x] for x in columns])
            book_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        flash("Libro agregado a tu biblioteca.","success")
        return redirect(url_for("libro_detalle",book_id=book_id))
    return render_template("book_form.html",book=None,fields=BOOK_FIELDS)

@app.route("/biblioteca/<int:book_id>")
def libro_detalle(book_id):
    with connect() as c: book=c.execute("SELECT * FROM books WHERE id=?",(book_id,)).fetchone()
    if not book: abort(404)
    return render_template("book_detail.html",book=book,fields=BOOK_FIELDS)

@app.route("/biblioteca/<int:book_id>/editar",methods=["GET","POST"])
def libro_editar(book_id):
    with connect() as c: book=c.execute("SELECT * FROM books WHERE id=?",(book_id,)).fetchone()
    if not book: abort(404)
    if request.method=="POST":
        data={field:request.form.get(field,"").strip() for field,label in BOOK_FIELDS}
        if not data["title"]:
            flash("El título del libro es obligatorio.","error"); return redirect(url_for("libro_editar",book_id=book_id))
        with connect() as c:
            c.execute("UPDATE books SET "+",".join(f"{k}=?" for k in data)+" WHERE id=?",list(data.values())+[book_id])
        flash("Ficha del libro actualizada.","success"); return redirect(url_for("libro_detalle",book_id=book_id))
    return render_template("book_form.html",book=book,fields=BOOK_FIELDS)

@app.route("/biblioteca/<int:book_id>/eliminar",methods=["POST"])
def libro_eliminar(book_id):
    with connect() as c:
        book=c.execute("SELECT * FROM books WHERE id=?",(book_id,)).fetchone()
        if book:
            if book["cover_name"] and (UPLOADS/book["cover_name"]).exists(): (UPLOADS/book["cover_name"]).unlink()
            c.execute("DELETE FROM books WHERE id=?",(book_id,))
    flash("Libro eliminado de tu biblioteca.","success")
    return redirect(url_for("biblioteca"))

# Password-protected personal diary
@app.route("/diario/configurar",methods=["GET","POST"])
def diario_configurar():
    if diary_is_setup():
        return redirect(url_for("diario_login"))
    if request.method=="POST":
        p1=request.form.get("password",""); p2=request.form.get("confirm_password","")
        if len(p1)<10:
            flash("Usa una contraseña de al menos 10 caracteres.","error")
        elif p1!=p2:
            flash("Las contraseñas no coinciden.","error")
        else:
            with connect() as c:
                c.execute("INSERT INTO diary_auth(id,password_hash) VALUES(1,?)",(generate_password_hash(p1),))
            session["diary_unlocked"]=True
            flash("Tu diario quedó configurado. Recuerda tu contraseña.","success")
            return redirect(url_for("diario"))
    return render_template("diary_setup.html")

@app.route("/diario/entrar",methods=["GET","POST"])
def diario_login():
    if not diary_is_setup():
        return redirect(url_for("diario_configurar"))
    if session.get("diary_unlocked"):
        return redirect(url_for("diario"))
    if request.method=="POST":
        password=request.form.get("password","")
        with connect() as c: row=c.execute("SELECT password_hash FROM diary_auth WHERE id=1").fetchone()
        if row and check_password_hash(row["password_hash"],password):
            session.clear()
            session["diary_unlocked"]=True
            flash("Diario desbloqueado.","success")
            return redirect(url_for("diario"))
        flash("Contraseña incorrecta.","error")
    return render_template("diary_login.html")

@app.route("/diario/salir",methods=["POST"])
def diario_salir():
    session.pop("diary_unlocked",None)
    flash("Cerraste el diario.","success")
    return redirect(url_for("index"))

@app.route("/diario")
@diary_required
def diario():
    q=request.args.get("buscar","").strip()
    sql="SELECT * FROM diary_entries"; args=[]
    if q:
        sql+=" WHERE title LIKE ? OR content LIKE ? OR mood LIKE ?"
        args=[f"%{q}%"]*3
    sql+=" ORDER BY entry_date DESC,id DESC"
    with connect() as c: entries=c.execute(sql,args).fetchall()
    return render_template("diary.html",entries=entries,q=q)

@app.route("/diario/nueva",methods=["GET","POST"])
@diary_required
def diario_nueva():
    if request.method=="POST":
        title=request.form.get("title","").strip()
        content=request.form.get("content","").strip()
        mood=request.form.get("mood","").strip()
        entry_date=request.form.get("entry_date","") or datetime.now().strftime("%Y-%m-%d")
        if not title or not content:
            flash("Escribe un título y el contenido de la entrada.","error")
            return redirect(url_for("diario_nueva"))
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with connect() as c:
            c.execute("INSERT INTO diary_entries(title,mood,entry_date,content,created_at,updated_at) VALUES(?,?,?,?,?,?)",
              (title,mood,entry_date,content,now,now))
            entry_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        flash("Tu entrada quedó guardada en el diario privado.","success")
        return redirect(url_for("diario_editar",entry_id=entry_id))
    return render_template("diary_form.html",entry=None)

@app.route("/diario/entrada/<int:entry_id>",methods=["GET","POST"])
@diary_required
def diario_editar(entry_id):
    with connect() as c: entry=c.execute("SELECT * FROM diary_entries WHERE id=?",(entry_id,)).fetchone()
    if not entry: abort(404)
    if request.method=="POST":
        title=request.form.get("title","").strip(); content=request.form.get("content","").strip()
        if not title or not content:
            flash("El título y el contenido son obligatorios.","error")
            return redirect(url_for("diario_editar",entry_id=entry_id))
        with connect() as c:
            c.execute("UPDATE diary_entries SET title=?,content=?,mood=?,entry_date=?,updated_at=? WHERE id=?",
              (title,content,request.form.get("mood","").strip(),request.form.get("entry_date",""),datetime.now().strftime("%Y-%m-%d %H:%M:%S"),entry_id))
        flash("Entrada actualizada.","success")
        return redirect(url_for("diario"))
    return render_template("diary_form.html",entry=entry)

@app.route("/diario/entrada/<int:entry_id>/eliminar",methods=["POST"])
@diary_required
def diario_eliminar(entry_id):
    with connect() as c: c.execute("DELETE FROM diary_entries WHERE id=?",(entry_id,))
    flash("Entrada eliminada.","success")
    return redirect(url_for("diario"))

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOADS,filename)

@app.route("/item/<int:item_id>/descargar")
def descargar(item_id):
    with connect() as c: item=c.execute("SELECT * FROM items WHERE id=?",(item_id,)).fetchone()
    if not item or not item["stored_name"]: abort(404)
    return send_from_directory(UPLOADS,item["stored_name"],as_attachment=True,download_name=item["original_name"])

@app.errorhandler(413)
def too_large(_):
    flash("El archivo supera el límite de 100 MB.","error")
    return redirect(request.referrer or url_for("index"))

init_db()
if __name__=="__main__":
    app.run(debug=True)
