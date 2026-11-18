"""Flask web app serving WCRF recipes parsed from the Obsidian vault.

Community features: user auth (login/register), admin vs user roles,
per-recipe memos with replies/likes/edit/delete, favorites, shares,
and notice/discussion boards.
"""

import json
import os
import random
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    jsonify,
    abort,
    send_from_directory,
    request,
    session,
    redirect,
    url_for,
    flash,
    g,
)
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = os.path.dirname(__file__)
RECIPES_JSON = os.path.join(APP_DIR, "recipes.json")
CATALOGUE_JSON = os.path.join(APP_DIR, "ingredients_catalogue.json")
DB_PATH = os.path.join(APP_DIR, "memos.db")
VAULT_DIR = "/Users/shrho/Documents/Obsidian Vault/WCRF Recipes"
VAULT_RECIPES_DIR = os.path.join(VAULT_DIR, "Recipes")
STATIC_IMAGES_DIR = os.path.join(APP_DIR, "static", "vault-images")

with open(RECIPES_JSON, "r", encoding="utf-8") as f:
    RECIPES = json.load(f)

with open(CATALOGUE_JSON, "r", encoding="utf-8") as f:
    CATALOGUE = json.load(f)

BY_ID = {r["id"]: r for r in RECIPES}
CATEGORIES = sorted({r["category"] for r in RECIPES if r["category"]})

SEASON_INGREDIENTS = {}
for entry in CATALOGUE:
    SEASON_INGREDIENTS.setdefault(entry["season"], []).append(entry["ingredient"].lower())

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            email TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id TEXT NOT NULL,
            author_id INTEGER,
            author_username TEXT,
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS memo_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memo_id INTEGER NOT NULL,
            author_id INTEGER,
            author_username TEXT,
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS memo_likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memo_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            UNIQUE (memo_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            recipe_id TEXT NOT NULL,
            UNIQUE (user_id, recipe_id)
        );

        CREATE TABLE IF NOT EXISTS shares (
            recipe_id TEXT PRIMARY KEY,
            share_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS boards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_id INTEGER NOT NULL,
            author_id INTEGER,
            author_username TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS post_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            author_id INTEGER,
            author_username TEXT,
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            is_deleted INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    db.commit()

    # Seed admin user if none exists
    admin = db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if not admin:
        now = datetime.now().isoformat()
        db.execute(
            "INSERT INTO users (username, password_hash, role, email, created_at, updated_at) VALUES (?, ?, 'admin', ?, ?, ?)",
            ("admin", generate_password_hash("admin123"), "admin@example.com", now, now),
        )
        db.commit()

    # Seed boards if empty
    board_count = db.execute("SELECT COUNT(*) AS c FROM boards").fetchone()["c"]
    if board_count == 0:
        now = datetime.now().isoformat()
        db.execute(
            "INSERT INTO boards (name, slug, description, created_at) VALUES (?, ?, ?, ?)",
            ("공지 및 행사", "notice", "WCRF 레시피북 공지사항 및 행사 안내", now),
        )
        db.execute(
            "INSERT INTO boards (name, slug, description, created_at) VALUES (?, ?, ?, ?)",
            ("토론", "discussion", "레시피에 대한 자유로운 토론 게시판", now),
        )
        db.commit()

    db.close()


init_db()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    if getattr(g, "_user_cache", None) is not None and g._user_cache.get("id") == uid:
        return g._user_cache
    db = get_db()
    row = db.execute("SELECT id, username, role, email FROM users WHERE id = ?", (uid,)).fetchone()
    db.close()
    if row is None:
        session.clear()
        return None
    user = dict(row)
    g._user_cache = user
    return user


@app.before_request
def load_user():
    g._user_cache = None
    g.user = current_user()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user or g.user.get("role") != "admin":
            flash("관리자만 접근할 수 있습니다.", "error")
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    return dict(current_user=g.user, category_label=lambda k: k.replace("-", " ").title())


def category_label(key):
    return key.replace("-", " ").title()


def current_season():
    month = datetime.now().month
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "fall"
    return "winter"


def find_seasonal_recipes(season):
    ingredients = SEASON_INGREDIENTS.get(season, [])
    if not ingredients:
        return []
    matches = []
    for r in RECIPES:
        blob = (
            " ".join(r.get("ingredients", []))
            + " "
            + r.get("title", "")
            + " "
            + r.get("description", "")
        ).lower()
        if any(ing in blob for ing in ingredients):
            matches.append(r)
    return matches


# ---------- Auth ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        db.close()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"] = row["id"]
            session["username"] = row["username"]
            session["role"] = row["role"]
            flash(f"환영합니다, {row['username']}님!", "success")
            nxt = request.args.get("next") or url_for("index")
            return redirect(nxt)
        flash("아이디 또는 비밀번호가 틀렸습니다.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        email = (request.form.get("email") or "").strip()
        if not username or not password:
            flash("아이디와 비밀번호를 입력해주세요.", "error")
            return render_template("register.html")
        db = get_db()
        exists = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if exists:
            db.close()
            flash("이미 사용 중인 아이디입니다.", "error")
            return render_template("register.html")
        now = datetime.now().isoformat()
        db.execute(
            "INSERT INTO users (username, password_hash, role, email, created_at, updated_at) VALUES (?, ?, 'user', ?, ?, ?)",
            (username, generate_password_hash(password), email, now, now),
        )
        db.commit()
        db.close()
        flash("회원가입이 완료되었습니다. 로그인해주세요.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("로그아웃되었습니다.", "success")
    return redirect(url_for("index"))


# ---------- Recipes ----------

@app.route("/")
def index():
    season = current_season()
    seasonal_recipes = find_seasonal_recipes(season)
    recommended = random.choice(seasonal_recipes) if seasonal_recipes else None
    db = get_db()
    favorite_counts = {}
    share_counts = {}
    if g.user:
        rows = db.execute(
            "SELECT recipe_id, COUNT(*) AS c FROM favorites WHERE user_id = ? GROUP BY recipe_id",
            (g.user["id"],),
        ).fetchall()
        favorite_counts = {r["recipe_id"]: r["c"] for r in rows}
    rows = db.execute("SELECT recipe_id, share_count FROM shares").fetchall()
    share_counts = {r["recipe_id"]: r["share_count"] for r in rows}
    db.close()
    return render_template(
        "index.html",
        recipes=RECIPES,
        categories=CATEGORIES,
        current_season=season,
        recommended=recommended,
        favorite_counts=favorite_counts,
        share_counts=share_counts,
    )


@app.route("/recipe/<recipe_id>")
def recipe_detail(recipe_id):
    recipe = BY_ID.get(recipe_id)
    if recipe is None:
        abort(404)
    db = get_db()
    # memos for this recipe
    memos = db.execute(
        """SELECT m.*, u.username AS author_username
           FROM memos m LEFT JOIN users u ON m.author_id = u.id
           WHERE m.recipe_id = ? AND m.is_deleted = 0
           ORDER BY m.created_at DESC""",
        (recipe_id,),
    ).fetchall()
    memo_list = []
    for m in memos:
        replies = db.execute(
            """SELECT r.*, u.username AS author_username
               FROM memo_replies r LEFT JOIN users u ON r.author_id = u.id
               WHERE r.memo_id = ? AND r.is_deleted = 0
               ORDER BY r.created_at ASC""",
            (m["id"],),
        ).fetchall()
        like_count = db.execute(
            "SELECT COUNT(*) AS c FROM memo_likes WHERE memo_id = ?", (m["id"],)
        ).fetchone()["c"]
        liked = False
        if g.user:
            liked = bool(
                db.execute(
                    "SELECT id FROM memo_likes WHERE memo_id = ? AND user_id = ?",
                    (m["id"], g.user["id"]),
                ).fetchone()
            )
        memo_list.append(
            {
                "id": m["id"],
                "content": m["content"],
                "author_id": m["author_id"],
                "author_username": m["author_username"] or m["author_id"],
                "created_at": m["created_at"],
                "updated_at": m["updated_at"],
                "replies": [
                    {
                        "id": r["id"],
                        "content": r["content"],
                        "author_id": r["author_id"],
                        "author_username": r["author_username"] or r["author_id"],
                        "created_at": r["created_at"],
                    }
                    for r in replies
                ],
                "like_count": like_count,
                "liked": liked,
            }
        )
    fav_row = db.execute(
        "SELECT id FROM favorites WHERE user_id = ? AND recipe_id = ?",
        (g.user["id"] if g.user else -1, recipe_id),
    ).fetchone()
    is_favorite = bool(fav_row)
    share_row = db.execute(
        "SELECT share_count FROM shares WHERE recipe_id = ?", (recipe_id,)
    ).fetchone()
    share_count = share_row["share_count"] if share_row else 0
    db.close()
    return render_template(
        "recipe.html",
        recipe=recipe,
        memos=memo_list,
        is_favorite=is_favorite,
        favorite_count=len([m for m in memos]),  # placeholder; real fav count below
        share_count=share_count,
    )


# ---------- Memos ----------

@app.route("/memos/new", methods=["POST"])
@login_required
def memo_new():
    data = request.get_json(silent=True) or {}
    recipe_id = data.get("recipe_id")
    content = (data.get("content") or "").strip()
    if not recipe_id or not content:
        return jsonify({"error": "내용을 입력해주세요."}), 400
    now = datetime.now().isoformat()
    db = get_db()
    cur = db.execute(
        "INSERT INTO memos (recipe_id, author_id, author_username, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (recipe_id, g.user["id"], g.user["username"], content, now, now),
    )
    db.commit()
    memo_id = cur.lastrowid
    db.close()
    return jsonify({"id": memo_id, "content": content, "author_username": g.user["username"], "created_at": now})


@app.route("/memos/<int:memo_id>/reply", methods=["POST"])
@login_required
def memo_reply(memo_id):
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "내용을 입력해주세요."}), 400
    db = get_db()
    memo = db.execute("SELECT id FROM memos WHERE id = ? AND is_deleted = 0", (memo_id,)).fetchone()
    if not memo:
        db.close()
        return jsonify({"error": "메모를 찾을 수 없습니다."}), 404
    now = datetime.now().isoformat()
    cur = db.execute(
        "INSERT INTO memo_replies (memo_id, author_id, author_username, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (memo_id, g.user["id"], g.user["username"], content, now),
    )
    db.commit()
    reply_id = cur.lastrowid
    db.close()
    return jsonify({"id": reply_id, "content": content, "author_username": g.user["username"], "created_at": now})


@app.route("/memos/<int:memo_id>/like", methods=["POST"])
@login_required
def memo_like(memo_id):
    db = get_db()
    memo = db.execute("SELECT id FROM memos WHERE id = ? AND is_deleted = 0", (memo_id,)).fetchone()
    if not memo:
        db.close()
        return jsonify({"error": "메모를 찾을 수 없습니다."}), 404
    existing = db.execute(
        "SELECT id FROM memo_likes WHERE memo_id = ? AND user_id = ?", (memo_id, g.user["id"])
    ).fetchone()
    if existing:
        db.execute("DELETE FROM memo_likes WHERE id = ?", (existing["id"],))
        liked = False
    else:
        db.execute(
            "INSERT INTO memo_likes (memo_id, user_id) VALUES (?, ?)", (memo_id, g.user["id"])
        )
        liked = True
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) AS c FROM memo_likes WHERE memo_id = ?", (memo_id,)
    ).fetchone()["c"]
    db.close()
    return jsonify({"liked": liked, "like_count": count})


@app.route("/memos/<int:memo_id>/edit", methods=["POST"])
@login_required
def memo_edit(memo_id):
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "내용을 입력해주세요."}), 400
    db = get_db()
    memo = db.execute(
        "SELECT author_id FROM memos WHERE id = ? AND is_deleted = 0", (memo_id,)
    ).fetchone()
    if not memo:
        db.close()
        return jsonify({"error": "메모를 찾을 수 없습니다."}), 404
    if memo["author_id"] != g.user["id"] and g.user.get("role") != "admin":
        db.close()
        return jsonify({"error": "수정 권한이 없습니다."}), 403
    now = datetime.now().isoformat()
    db.execute(
        "UPDATE memos SET content = ?, updated_at = ? WHERE id = ?", (content, now, memo_id)
    )
    db.commit()
    db.close()
    return jsonify({"content": content, "updated_at": now})


@app.route("/memos/<int:memo_id>/delete", methods=["POST"])
@login_required
def memo_delete(memo_id):
    db = get_db()
    memo = db.execute(
        "SELECT author_id FROM memos WHERE id = ? AND is_deleted = 0", (memo_id,)
    ).fetchone()
    if not memo:
        db.close()
        return jsonify({"error": "메모를 찾을 수 없습니다."}), 404
    if memo["author_id"] != g.user["id"] and g.user.get("role") != "admin":
        db.close()
        return jsonify({"error": "삭제 권한이 없습니다."}), 403
    db.execute("UPDATE memos SET is_deleted = 1 WHERE id = ?", (memo_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ---------- Favorites / Shares ----------

@app.route("/recipe/<recipe_id>/favorite", methods=["POST"])
@login_required
def recipe_favorite(recipe_id):
    db = get_db()
    existing = db.execute(
        "SELECT id FROM favorites WHERE user_id = ? AND recipe_id = ?", (g.user["id"], recipe_id)
    ).fetchone()
    if existing:
        db.execute("DELETE FROM favorites WHERE id = ?", (existing["id"],))
        favorited = False
    else:
        db.execute(
            "INSERT INTO favorites (user_id, recipe_id) VALUES (?, ?)", (g.user["id"], recipe_id)
        )
        favorited = True
    db.commit()
    count = db.execute(
        "SELECT COUNT(*) AS c FROM favorites WHERE recipe_id = ?", (recipe_id,)
    ).fetchone()["c"]
    db.close()
    return jsonify({"favorited": favorited, "favorite_count": count})


@app.route("/recipe/<recipe_id>/share", methods=["POST"])
def recipe_share(recipe_id):
    db = get_db()
    row = db.execute("SELECT share_count FROM shares WHERE recipe_id = ?", (recipe_id,)).fetchone()
    if row:
        db.execute(
            "UPDATE shares SET share_count = share_count + 1 WHERE recipe_id = ?", (recipe_id,)
        )
        count = row["share_count"] + 1
    else:
        db.execute("INSERT INTO shares (recipe_id, share_count) VALUES (?, 1)", (recipe_id,))
        count = 1
    db.commit()
    db.close()
    return jsonify({"share_count": count})


# ---------- Admin ----------

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    stats = {
        "users": db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
        "recipes": len(RECIPES),
        "memos": db.execute("SELECT COUNT(*) AS c FROM memos WHERE is_deleted = 0").fetchone()["c"],
        "posts": db.execute("SELECT COUNT(*) AS c FROM posts WHERE is_deleted = 0").fetchone()["c"],
    }
    recent_users = db.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    db.close()
    return render_template("admin_dashboard.html", stats=stats, recent_users=recent_users)


@app.route("/admin/users")
@admin_required
def admin_users():
    db = get_db()
    users = db.execute(
        "SELECT id, username, role, email, created_at FROM users ORDER BY id"
    ).fetchall()
    db.close()
    return render_template("admin_users.html", users=users)


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_user(user_id):
    db = get_db()
    user = db.execute("SELECT id, username, role, email FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        db.close()
        abort(404)
    if request.method == "POST":
        role = request.form.get("role", "user")
        email = (request.form.get("email") or "").strip()
        now = datetime.now().isoformat()
        db.execute(
            "UPDATE users SET role = ?, email = ?, updated_at = ? WHERE id = ?",
            (role, email, now, user_id),
        )
        db.commit()
        db.close()
        flash("사용자 정보가 수정되었습니다.", "success")
        return redirect(url_for("admin_users"))
    db.close()
    return render_template("user_edit.html", user=user, is_admin_page=True)


# ---------- User profile ----------

@app.route("/user/profile", methods=["GET", "POST"])
@login_required
def user_profile():
    db = get_db()
    user = db.execute("SELECT id, username, role, email FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        now = datetime.now().isoformat()
        if password:
            db.execute(
                "UPDATE users SET email = ?, password_hash = ?, updated_at = ? WHERE id = ?",
                (email, generate_password_hash(password), now, g.user["id"]),
            )
        else:
            db.execute(
                "UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
                (email, now, g.user["id"]),
            )
        db.commit()
        db.close()
        flash("프로필이 수정되었습니다.", "success")
        return redirect(url_for("user_profile"))
    db.close()
    return render_template("user_edit.html", user=user, is_admin_page=False)


# ---------- Boards ----------

@app.route("/boards")
def board_list():
    db = get_db()
    boards = db.execute(
        """SELECT b.*, COUNT(p.id) AS post_count
           FROM boards b LEFT JOIN posts p ON p.board_id = b.id AND p.is_deleted = 0
           GROUP BY b.id ORDER BY b.id"""
    ).fetchall()
    db.close()
    return render_template("board_list.html", boards=boards)


@app.route("/boards/<slug>")
def board_view(slug):
    db = get_db()
    board = db.execute("SELECT * FROM boards WHERE slug = ?", (slug,)).fetchone()
    if not board:
        db.close()
        abort(404)
    posts = db.execute(
        """SELECT p.*, COUNT(pr.id) AS reply_count
           FROM posts p LEFT JOIN post_replies pr ON pr.post_id = p.id AND pr.is_deleted = 0
           WHERE p.board_id = ? AND p.is_deleted = 0
           GROUP BY p.id ORDER BY p.created_at DESC""",
        (board["id"],),
    ).fetchall()
    db.close()
    return render_template("board_view.html", board=board, posts=posts)


@app.route("/boards/<slug>/new", methods=["GET", "POST"])
@login_required
def board_new(slug):
    db = get_db()
    board = db.execute("SELECT * FROM boards WHERE slug = ?", (slug,)).fetchone()
    if not board:
        db.close()
        abort(404)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = (request.form.get("content") or "").strip()
        if not title or not content:
            flash("제목과 내용을 입력해주세요.", "error")
            db.close()
            return render_template("post_form.html", board=board)
        now = datetime.now().isoformat()
        cur = db.execute(
            "INSERT INTO posts (board_id, author_id, author_username, title, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (board["id"], g.user["id"], g.user["username"], title, content, now, now),
        )
        db.commit()
        db.close()
        flash("글이 등록되었습니다.", "success")
        return redirect(url_for("board_view", slug=slug))
    db.close()
    return render_template("post_form.html", board=board)


@app.route("/posts/<int:post_id>")
def post_detail(post_id):
    db = get_db()
    post = db.execute(
        """SELECT p.*, b.name AS board_name, b.slug AS board_slug
           FROM posts p JOIN boards b ON p.board_id = b.id
           WHERE p.id = ? AND p.is_deleted = 0""",
        (post_id,),
    ).fetchone()
    if not post:
        db.close()
        abort(404)
    replies = db.execute(
        """SELECT r.*, u.username AS author_username
           FROM post_replies r LEFT JOIN users u ON r.author_id = u.id
           WHERE r.post_id = ? AND r.is_deleted = 0
           ORDER BY r.created_at ASC""",
        (post_id,),
    ).fetchall()
    db.close()
    return render_template("post_detail.html", post=post, replies=replies)


@app.route("/posts/<int:post_id>/reply", methods=["POST"])
@login_required
def post_reply(post_id):
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "내용을 입력해주세요."}), 400
    db = get_db()
    post = db.execute("SELECT id FROM posts WHERE id = ? AND is_deleted = 0", (post_id,)).fetchone()
    if not post:
        db.close()
        return jsonify({"error": "글을 찾을 수 없습니다."}), 404
    now = datetime.now().isoformat()
    cur = db.execute(
        "INSERT INTO post_replies (post_id, author_id, author_username, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (post_id, g.user["id"], g.user["username"], content, now),
    )
    db.commit()
    reply_id = cur.lastrowid
    db.close()
    return jsonify({"id": reply_id, "content": content, "author_username": g.user["username"], "created_at": now})


@app.route("/catalogue")
def catalogue():
    seasons = ["spring", "summer", "fall", "winter", "all year"]
    return render_template("catalogue.html", catalogue=CATALOGUE, seasons=seasons)


@app.route("/image/<path:filename>")
def serve_image(filename):
    """Serve recipe images from static copy first, fall back to vault (dev)."""
    static_path = os.path.join(STATIC_IMAGES_DIR, filename)
    if os.path.isfile(static_path):
        return send_from_directory(STATIC_IMAGES_DIR, filename)
    for root, _dirs, files in os.walk(VAULT_RECIPES_DIR):
        if filename in files:
            return send_from_directory(root, filename)
    abort(404)


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
