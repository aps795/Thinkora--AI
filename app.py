import cgi
import io
import json
import os
import re
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

import requests


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(ROOT_DIR, "thinkora.html")
DB_PATH = os.path.join(ROOT_DIR, "study_history.db")
UPLOAD_DIR = os.path.join(ROOT_DIR, "uploads")
KNOWLEDGE_PATH = os.path.join(ROOT_DIR, "knowledge.json")


class StudyHistoryStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

    def save(self, role: str, message: str):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO chats(role, message, created_at) VALUES (?, ?, datetime('now'))",
            (role, message),
        )
        conn.commit()
        conn.close()

    def get_recent(self, limit: int = 8):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT role, message, created_at FROM chats ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [{"role": role, "message": message, "created_at": created_at} for role, message, created_at in rows[::-1]]


class SmartAssistant:
    def __init__(self):
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.provider = os.getenv("AI_PROVIDER", "openai" if self.openai_key else "gemini" if self.gemini_key else "local")
        self.knowledge = self._load_knowledge()

    def _load_knowledge(self):
        try:
            with open(KNOWLEDGE_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return {"general_science": [], "indian_gk": []}

    def _lookup_knowledge(self, message: str) -> Optional[str]:
        text = message.lower().strip()
        for item in self.knowledge.get("general_science", []) + self.knowledge.get("indian_gk", []):
            if any(keyword in text for keyword in item.get("keywords", [])):
                return item.get("answer")
        return None

    def get_reply(self, message: str) -> str:
        knowledge_reply = self._lookup_knowledge(message)
        if knowledge_reply:
            return knowledge_reply
        if self.provider == "openai" and self.openai_key:
            return self._call_openai(message)
        if self.provider == "gemini" and self.gemini_key:
            return self._call_gemini(message)
        return self._fallback_reply(message)

    def _call_openai(self, message: str) -> str:
        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You are Thinkora AI, a smart study and general assistant for students."},
                        {"role": "user", "content": message},
                    ],
                    "temperature": 0.7,
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            return payload["choices"][0]["message"]["content"].strip()
        except Exception:
            return self._fallback_reply(message)

    def _call_gemini(self, message: str) -> str:
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_key}",
                json={
                    "contents": [{"parts": [{"text": message}]}],
                    "generationConfig": {"temperature": 0.7},
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            return self._fallback_reply(message)

    def _fallback_reply(self, message: str) -> str:
        text = message.lower().strip()
        if any(keyword in text for keyword in ["quiz", "mcq", "test"]):
            return "I can create a quick quiz with 5 MCQs, explain the answer key, and turn it into a practice set for exam prep."
        if any(keyword in text for keyword in ["general science", "science", "physics", "chemistry", "biology"]):
            return "I can explain general science topics such as photosynthesis, gravity, the human body, and the solar system in simple words."
        if any(keyword in text for keyword in ["gk", "india", "indian"]):
            return "I can answer Indian GK questions about the capital, national symbols, independence, rivers, and important leaders."
        if any(keyword in text for keyword in ["note", "notes", "revision"]):
            return "I can generate structured study notes, key points, and a simple revision checklist for your topic."
        if any(keyword in text for keyword in ["math", "equation", "solve"]):
            return "I can solve problems step by step and explain the logic clearly so it feels easy to follow."
        if any(keyword in text for keyword in ["code", "python", "program"]):
            return "I can help write code, debug errors, and explain each part in a beginner-friendly way."
        if any(keyword in text for keyword in ["pdf", "summary", "document"]):
            return "Upload a PDF or document and I can summarize it, extract key points, and answer questions from it."
        if any(keyword in text for keyword in ["ncert", "chapter", "subject"]):
            return "I can explain NCERT topics in simple language, give examples, and turn them into notes or flashcards."
        return (
            "I’m Thinkora AI, your smart study and general assistant. I can explain concepts, create notes, solve quiz questions, "
            "help with coding, and support your learning in real time."
        )


assistant = SmartAssistant()
history_store = StudyHistoryStore(DB_PATH)
os.makedirs(UPLOAD_DIR, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "ThinkoraAI/1.0"

    def do_OPTIONS(self):
        self._send_json(200, {})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(200, {"status": "ok", "provider": assistant.provider})
            return
        if parsed.path == "/api/history":
            limit = 8
            if parsed.query:
                try:
                    limit = int(parsed.query.split("=", 1)[1])
                except (IndexError, ValueError):
                    limit = 8
            self._send_json(200, {"history": history_store.get_recent(limit)})
            return
        if parsed.path in ["/", "/thinkora.html"]:
            self._serve_file(HTML_FILE)
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            self._handle_chat()
            return
        if parsed.path == "/api/stream":
            self._handle_stream()
            return
        if parsed.path == "/api/upload":
            self._handle_upload()
            return
        self._send_json(404, {"error": "not found"})

    def _handle_chat(self):
        payload = self._read_json()
        message = (payload or {}).get("message", "")
        history_store.save("user", message)
        reply = assistant.get_reply(message)
        history_store.save("assistant", reply)
        self._send_json(200, {"reply": reply, "provider": assistant.provider})

    def _handle_stream(self):
        payload = self._read_json()
        message = (payload or {}).get("message", "")
        history_store.save("user", message)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        full_reply = assistant.get_reply(message)
        history_store.save("assistant", full_reply)
        words = re.findall(r"\S+\s*", full_reply)
        if not words:
            words = [full_reply]
        def _safe_write(data: bytes) -> bool:
            try:
                self.wfile.write(data)
                try:
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    # client likely disconnected while flushing
                    return False
                return True
            except (BrokenPipeError, ConnectionResetError, OSError):
                # client disconnected; stop streaming
                return False

        for word in words:
            chunk = word if word.endswith(" ") else word + " "
            payload = f"data: {json.dumps({'text': chunk})}\n\n".encode("utf-8")
            if not _safe_write(payload):
                self.log_message("client disconnected during stream, stopping send")
                return
            time.sleep(0.03)

        if not _safe_write(b"data: [DONE]\n\n"):
            self.log_message("client disconnected before final [DONE]")

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json(400, {"error": "expected multipart/form-data"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        form = cgi.FieldStorage(
            fp=io.BytesIO(raw),
            headers={"content-type": content_type},
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type},
            keep_blank_values=True,
        )

        if "file" not in form:
            self._send_json(400, {"error": "no file provided"})
            return

        uploaded = form["file"]
        if not uploaded.filename:
            self._send_json(400, {"error": "empty filename"})
            return

        safe_name = os.path.basename(uploaded.filename)
        save_path = os.path.join(UPLOAD_DIR, f"{int(time.time())}_{safe_name}")
        with open(save_path, "wb") as handle:
            handle.write(uploaded.file.read())

        history_store.save("upload", f"Uploaded document: {safe_name}")
        self._send_json(
            200,
            {
                "saved": True,
                "filename": safe_name,
                "path": save_path,
                "summary": f"Saved {safe_name} for later study review and note generation.",
            },
        )

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def _serve_file(self, path: str):
        with open(path, "rb") as fh:
            content = fh.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
    print("Thinkora AI server running at http://127.0.0.1:8000")
    server.serve_forever()
