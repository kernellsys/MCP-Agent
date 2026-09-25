                      
                       
"""
Mudin-Dev - Versao Refatorada e Melhorada
Mantem 100% da funcionalidade original, mas com:
- sem duplicacao de codigo
- tipagem, dataclasses e separacao de responsabilidades
- safe_path realmente seguro (is_relative_to)
- escrita WASM eficiente
- tratamento de erros sem bare except
- diff tracker sem global mutavel solto
- console UTF-8 robusto
- tool registry extensivel
"""
from __future__ import annotations

import base64
import datetime
import difflib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

                                                                             
                 
                                                                             
def ensure_pkg(import_name: str, pip_name: Optional[str] = None) -> None:
    pip_name = pip_name or import_name
    try:
        __import__(import_name)
    except ImportError:
        print(f"[deps] instalando {pip_name}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pip_name, "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

ensure_pkg("httpx")
ensure_pkg("wasmtime")
ensure_pkg("colorama")

import httpx
import wasmtime

try:
    import colorama
    colorama.just_fix_windows_console()
    colorama.init()
except Exception:
    pass

                                                                             
                               
                                                                             
def setup_console_utf8() -> None:
    if os.name != "nt":
        return
    try:
        os.system("chcp 65001 > nul")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        m = ctypes.c_ulong()
        if k.GetConsoleMode(h, ctypes.byref(m)):
            k.SetConsoleMode(h, m.value | 0x0004)                                      
    except Exception:
        pass

setup_console_utf8()

USE_COLOR = os.getenv("NO_COLOR") != "1"
                                                                
USE_UNICODE = False

class Colors:
    @staticmethod
    def _wrap(code: str, t: str) -> str:
        return f"\x1b[{code}m{t}\x1b[0m" if USE_COLOR else t
    @classmethod
    def orange(cls, t: str) -> str: return cls._wrap("38;5;208", t)
    @classmethod
    def gray(cls, t: str) -> str: return cls._wrap("90", t)
    @classmethod
    def green(cls, t: str) -> str: return cls._wrap("32", t)
    @classmethod
    def red(cls, t: str) -> str: return cls._wrap("31", t)
    @classmethod
    def cyan(cls, t: str) -> str: return cls._wrap("36", t)
    @classmethod
    def bold(cls, t: str) -> str: return cls._wrap("1", t)

                                                              
SYM = {
    "dot": ".",
    "star": "*",
    "star2": "*",
    "star3": "*",
    "star4": "*",
    "star5": "*",
    "bullet": "*",
    "circle": "o",
    "corner": ">",
    "box_tl": "+",
    "box_tr": "+",
    "box_bl": "+",
    "box_br": "+",
    "box_h": "-",
    "box_v": "|",
    "claude_dot": "o",
    "claude_tool": ">",
    "claude_bullet": "*",
}

                                                                             
                                                                     
                                                                             
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

WASM_PATH = CONFIG_DIR / "sha3_wasm_bg.wasm"
WASM_URL = "https://raw.githubusercontent.com/sums001/Deepseek-API/main/deepseek/sha3_wasm_bg.wasm"
BASE_URL = "https://chat.deepseek.com"
JSON_PATH = CONFIG_DIR / "deepseek_data.json"
WORKSPACE = BASE_DIR / "workspace"
WORKSPACE.mkdir(parents=True, exist_ok=True)
WORKFLOW_PATH = CONFIG_DIR / "workflow.json"

@dataclass
class AppConfig:
    base_url: str = BASE_URL
    wasm_path: Path = WASM_PATH
    wasm_url: str = WASM_URL
    workspace: Path = WORKSPACE
    json_path: Path = JSON_PATH
    config_dir: Path = CONFIG_DIR
    workflow_path: Path = WORKFLOW_PATH
    completion_path: str = "/api/v0/chat/completion"
    challenge_path: str = "/api/v0/chat/create_pow_challenge"
    session_path: str = "/api/v0/chat_session/create"

CONFIG = AppConfig()

class JsonStorage:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"usertoken": "", "history": []}
        try:
            raw = self.path.read_text(encoding="utf-8")
            if not raw.strip():
                raise ValueError("arquivo vazio")
            data = json.loads(raw)
            data.setdefault("usertoken", "")
            data.setdefault("history", [])
            return data
        except Exception:
            try:
                self.path.unlink(missing_ok=True)
            except Exception:
                pass
            return {"usertoken": "", "history": []}

    def save(self, data: Dict[str, Any]) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

                                                                             
          
                                                                             
def clean_token(raw: str) -> str:
    t = raw.strip().strip('"').strip("'").replace("Bearer ", "").strip()
    if not t:
        return t
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            if isinstance(obj, dict) and "value" in obj:
                return str(obj["value"]).strip()
        except json.JSONDecodeError:
            pass
    return t

def clean_output(text: str) -> str:
                                                    
    text = re.sub(r'```.*?```', lambda m: m.group(0)[3:-3].strip(), text, flags=re.DOTALL)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    text = re.sub(r'(?<!\w)\*(.*?)\*(?!\w)', r'\1', text)
    text = re.sub(r'`(.*?)`', r'\1', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def safe_path(p: str) -> Path:
    """Seguro contra path traversal, usando is_relative_to."""
    p_str = str(p).replace("\\", "/").lstrip("/")
    if p_str.startswith("workspace/"):
        p_str = p_str[len("workspace/"):]
                         
    full = (CONFIG.workspace / p_str).resolve()
    try:
                     
        if not full.is_relative_to(CONFIG.workspace.resolve()):
            raise RuntimeError(f"fora de workspace: {p}")
    except AttributeError:
                      
        if not str(full).startswith(str(CONFIG.workspace.resolve())):
            raise RuntimeError(f"fora de workspace: {p}")
    return full

def ensure_wasm() -> None:
    if CONFIG.wasm_path.exists():
        try:
            if CONFIG.wasm_path.stat().st_size < 1000:
                CONFIG.wasm_path.unlink(missing_ok=True)
            else:
                return
        except Exception:
            pass
    print(f"[wasm] baixando {CONFIG.wasm_url}...")
    try:
        CONFIG.config_dir.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(CONFIG.wasm_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(CONFIG.wasm_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        if CONFIG.wasm_path.stat().st_size < 1000:
            CONFIG.wasm_path.unlink(missing_ok=True)
            raise RuntimeError("wasm baixado corrompido")
    except Exception as e:
        try:
            CONFIG.wasm_path.unlink(missing_ok=True)
        except Exception:
            pass
        print(f"[wasm] erro: {e}")
        raise

                                                                             
                             
                                                                             
class WorkspaceManager:
    def __init__(self, root: Path):
        self.root = root

    def list_files(self) -> str:
        out: List[str] = []
        for cur_root, dirs, files in os.walk(self.root):
                           
            dirs[:] = [d for d in dirs if d not in {"__pycache__", ".git", "node_modules", ".venv"}]
            rel = os.path.relpath(cur_root, self.root)
            out.append("workspace/" if rel == "." else f"{rel}/")
            for f in sorted(files):
                rel_file = os.path.join(rel, f) if rel != "." else f
                out.append(f"  {rel_file}")
        return "\n".join(out) if out else "workspace/ (vazio)"

@dataclass
class DiffEntry:
    path: str
    lines: List[str]

class DiffTracker:
    def __init__(self, max_preview: int = 5):
        self.entries: List[DiffEntry] = []
        self.max_preview = max_preview

    def collect(self, old_text: str, new_text: str, path: str) -> None:
        old = old_text.splitlines() if old_text else []
        new = new_text.splitlines() if new_text else []
        diff = difflib.unified_diff(old, new, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=0)
        filtered = [l for l in diff if not l.startswith("---") and not l.startswith("+++") and not l.startswith("@@")]
        if filtered:
            self.entries.append(DiffEntry(path, filtered))

    def show_and_clear(self) -> None:
        if not self.entries:
            return
        print(f"\n  {Colors.gray('Diffs:')}")
        for entry in self.entries:
            print(f"  {Colors.gray(SYM['corner'])} {Colors.gray(entry.path)}")
            for line in entry.lines[:self.max_preview]:
                if line.startswith("+"):
                    print(f"    {Colors.green(line)}")
                elif line.startswith("-"):
                    print(f"    {Colors.red(line)}")
                else:
                    print(f"    {line}")
            if len(entry.lines) > self.max_preview:
                print(f"    {Colors.gray('+' + str(len(entry.lines)-self.max_preview) + ' linhas...')}")
        print()
        self.entries.clear()

workspace_mgr = WorkspaceManager(CONFIG.workspace)
diff_tracker = DiffTracker()

                                                                             
                                          
                                                                             
                                             

@dataclass
class WorkflowTask:
    id: str
    description: str
    stage: str = "CRIAR"                                                                  
    file_path: str = ""
    attempts: int = 0
    last_error: str = ""
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    logs: List[str] = field(default_factory=list)

class WorkflowPersistence:
    """
    Persistencia do workflow autonomo:
    ETAPA 1: CRIAR -> escreve arquivo
    ETAPA 2: ANALISAR -> TESTAR -> verifica sintaxe e roda testes
    ETAPA 3: CORRIGIR (se erro) ou APRESENTAR -> ABRIR (se ok)
    """
    STAGES = ["CRIAR", "ANALISAR", "TESTAR", "CORRIGIR", "APRESENTAR", "ABRIR", "CONCLUIDO"]

    def __init__(self, path: Path):
        self.path = path
        self.tasks: Dict[str, WorkflowTask] = {}
        self.current_task_id: Optional[str] = None
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            if not raw.strip():
                raise ValueError("vazio")
            data = json.loads(raw)
            self.current_task_id = data.get("current_task_id")
            for tid, tdata in data.get("tasks", {}).items():
                self.tasks[tid] = WorkflowTask(**tdata)
        except Exception:
            try:
                self.path.unlink(missing_ok=True)
            except Exception:
                pass
            self.tasks = {}
            self.current_task_id = None

    def save(self) -> None:
        try:
            data = {
                "current_task_id": self.current_task_id,
                "tasks": {tid: {
                    "id": t.id,
                    "description": t.description,
                    "stage": t.stage,
                    "file_path": t.file_path,
                    "attempts": t.attempts,
                    "last_error": t.last_error,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                    "logs": t.logs[-20:]                          
                } for tid, t in self.tasks.items()}
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def create_task(self, description: str, file_path: str = "") -> WorkflowTask:
        tid = f"task_{int(time.time())}"
        task = WorkflowTask(id=tid, description=description, file_path=file_path, stage="CRIAR")
        self.tasks[tid] = task
        self.current_task_id = tid
        self.log(tid, f"[CRIAR] Tarefa criada: {description} -> {file_path}")
        self.save()
        return task

    def get_current(self) -> Optional[WorkflowTask]:
        if self.current_task_id and self.current_task_id in self.tasks:
            return self.tasks[self.current_task_id]
        return None

    def set_stage(self, task_id: str, stage: str, error: str = "") -> None:
        if task_id not in self.tasks:
            return
        task = self.tasks[task_id]
        task.stage = stage
        task.updated_at = datetime.datetime.now().isoformat()
        if error:
            task.last_error = error[:2000]
            task.attempts += 1
        self.log(task_id, f"[{stage}] {error[:200] if error else 'ok'}")
        self.save()
                                                                   

    def log(self, task_id: str, msg: str) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].logs.append(f"{datetime.datetime.now().isoformat()} {msg}")

    def list_tasks(self) -> str:
        if not self.tasks:
            return "nenhuma tarefa no workflow"
        out = []
        for t in self.tasks.values():
            out.append(f"{t.id[:8]} | {t.stage:12} | {t.file_path} | {t.description[:40]} | tentativas:{t.attempts}")
        return "\n".join(out)

workflow = WorkflowPersistence(CONFIG.workflow_path)

                                                                             
          
                                                                             
ToolFunc = Callable[[Dict[str, Any]], str]

def tool_list_files(_: Dict[str, Any]) -> str:
    return workspace_mgr.list_files()

def tool_read_file(args: Dict[str, Any]) -> str:
    path = args.get("path")
    if not path:
        return "erro: path obrigatorio"
    fp = safe_path(str(path))
    if not fp.exists():
        return f"nao existe: {path}"
    try:
        return fp.read_text(encoding="utf-8", errors="ignore")[:15000]
    except OSError as e:
        return f"erro leitura {path}: {e}"

def tool_write_file(args: Dict[str, Any]) -> str:
    path = args.get("path")
    content = args.get("content", "")
    description = args.get("description") or args.get("desc") or f"criar {path}"
    if not path:
        return "erro: path obrigatorio"
    fp = safe_path(str(path))
    old = ""
    is_new = not fp.exists()
    if not is_new:
        try:
            old = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            old = ""
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")
    diff_tracker.collect("" if is_new else old, content, str(path))

                                         
    try:
        current = workflow.get_current()
        if not current or current.file_path != str(path):
            task = workflow.create_task(description, str(path))
        else:
            task = current
            task.file_path = str(path)
        workflow.set_stage(task.id, "CRIAR")
                                                 
        workflow.set_stage(task.id, "ANALISAR")
    except Exception:
        pass

    return f"ok {path} ({'novo' if is_new else 'editado'}) [workflow: CRIAR->ANALISAR]"

def tool_edit_file(args: Dict[str, Any]) -> str:
    path = args.get("path")
    old_txt = args.get("old_text")
    new_txt = args.get("new_text", "")
    if not path or old_txt is None:
        return "erro: path e old_text obrigatorios"
    fp = safe_path(str(path))
    if not fp.exists():
        return f"nao existe: {path}"
    full = fp.read_text(encoding="utf-8", errors="ignore")
    if old_txt not in full:
        return "old_text nao encontrado"
    new_full = full.replace(old_txt, new_txt, 1)
    fp.write_text(new_full, encoding="utf-8")
    diff_tracker.collect(full, new_full, str(path))
    return f"editado {path}"

def tool_delete_file(args: Dict[str, Any]) -> str:
    path = args.get("path")
    if not path:
        return "erro: path obrigatorio"
    fp = safe_path(str(path))
    old = ""
    if fp.exists() and fp.is_file():
        try:
            old = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            old = ""
    try:
        if fp.is_dir():
            shutil.rmtree(fp)
        else:
            fp.unlink(missing_ok=True)
        diff_tracker.collect(old, "", str(path))
        return f"deletado {path}"
    except OSError as e:
        return f"erro deletar {path}: {e}"

def tool_rename_file(args: Dict[str, Any]) -> str:
    src = args.get("from") or args.get("path")
    dst = args.get("to")
    if not src or not dst:
        return "erro: from e to obrigatorios"
    s = safe_path(str(src))
    d = safe_path(str(dst))
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        s.rename(d)
        return f"{src} -> {dst}"
    except OSError as e:
        return f"erro rename: {e}"

def tool_mkdir(args: Dict[str, Any]) -> str:
    p = args.get("path")
    if not p:
        return "erro: path obrigatorio"
    safe_path(str(p)).mkdir(parents=True, exist_ok=True)
    return f"dir {p}"

def tool_shell(args: Dict[str, Any]) -> str:
    cmd = args.get("command") or args.get("cmd") or ""
    if not cmd:
        return "erro: command obrigatorio"
    cwd_arg = args.get("cwd") or str(CONFIG.workspace)
    try:
        cwd_path = Path(cwd_arg)
        if not cwd_path.is_absolute():
            cwd_path = safe_path(cwd_arg) if cwd_arg.startswith("workspace") else CONFIG.workspace / cwd_arg
        cwd_str = str(cwd_path.resolve() if cwd_path.exists() else CONFIG.workspace.resolve())
    except Exception:
        cwd_str = str(CONFIG.workspace.resolve())

    is_win = os.name == "nt"
    full_cmd = ["powershell", "-NoProfile", "-Command", cmd] if is_win else ["bash", "-lc", cmd]
    try:
        result = subprocess.run(
            full_cmd, cwd=cwd_str, capture_output=True, text=True,
            timeout=args.get("timeout", 60), shell=False
        )
        out = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return f"exit={result.returncode}\n{out.strip()[:8000]}"
    except subprocess.TimeoutExpired:
        return "erro: timeout"
    except Exception as e:
        return f"erro {e}"

def _clean_html_to_text(html: str) -> str:
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def tool_fetch(args: Dict[str, Any]) -> str:
    url = args.get("url") or args.get("link") or ""
    if not url:
        return "url vazia"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    }
    def try_get(u: str) -> str:
        r = httpx.get(u, timeout=15, follow_redirects=True, headers=headers)
        r.raise_for_status()
        txt = _clean_html_to_text(r.text)[:10000]
        if len(txt) < 200 or ("captcha" in txt.lower() and len(txt) < 1000):
            raise RuntimeError("conteudo bloqueado ou muito curto")
        return txt

    try:
        txt = try_get(url)
        return f"fetch {url}: {txt[:8000]}"
    except Exception:
                             
        try:
            bypass = f"https://api.allorigins.win/raw?url={urllib.parse.quote(url)}"
            txt = try_get(bypass)
            return f"fetch {url} via bypass: {txt[:8000]}"
        except Exception as e:
            return f"erro fetch {url}: {e}"

def tool_search(args: Dict[str, Any]) -> str:
    q = args.get("query") or args.get("q") or ""
    if not q:
        return "query vazia"
    try:
        url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(q)}"
        r = httpx.get(
            url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "pt-BR"},
            follow_redirects=True
        )
        txt = _clean_html_to_text(r.text)[:12000]
        if len(txt) < 200:
            return f"web search '{q}' sem resultados"
        return f"web search '{q}': {txt[:8000]}"
    except Exception as e:
        return f"erro search {q}: {e}"

def tool_grep(args: Dict[str, Any]) -> str:
    q = args.get("query") or args.get("q") or ""
    if not q:
        return "query vazia"
    try:
        url = f"https://grep.app/search?q={urllib.parse.quote(q)}"
        r = httpx.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        txt = _clean_html_to_text(r.text)[:8000]
        return f"grep.app '{q}': {txt[:6000]}"
    except Exception as e:
        return f"erro grep {q}: {e}"

def tool_google_github(args: Dict[str, Any]) -> str:
    q = args.get("query") or args.get("q") or args.get("intext") or ""
    if not q:
        return "query vazia"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    }
    def try_get(u: str) -> str:
        r = httpx.get(u, timeout=15, follow_redirects=True, headers=headers)
        r.raise_for_status()
        txt = _clean_html_to_text(r.text)[:12000]
        if len(txt) < 200:
            raise RuntimeError("bloqueado ou curto")
        return txt
    google_url = f"https://www.google.com/search?q={urllib.parse.quote(q)}"
    try:
        txt = try_get(google_url)
        if "detected unusual traffic" in txt.lower() or "captcha" in txt.lower() or len(txt) < 500:
            raise RuntimeError("google bloqueou")
        return f"google '{q}': {txt[:8000]} | url: {google_url}"
    except Exception:
        try:
            bypass_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote(google_url)}"
            txt = try_get(bypass_url)
            return f"google '{q}' via bypass: {txt[:8000]}"
        except Exception as e:
            try:
                ddg_url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(q)}"
                txt = try_get(ddg_url)
                return f"ddg fallback '{q}': {txt[:8000]}"
            except Exception as e2:
                return f"erro google '{q}': {e} / {e2}"

def tool_google_site(args: Dict[str, Any]) -> str:
    site = args.get("site") or args.get("domain") or ""
    q = args.get("query") or args.get("q") or args.get("intext") or ""
    if not q and not site:
        return "query vazia - use site e query, ex: site=github.com query=mta parser"
    if site and q:
        full_q = f"site:{site} {q}"
    elif site:
        full_q = f"site:{site}"
    else:
        full_q = q
    return tool_google_github({"query": full_q})                                                                                                   
def tool_workflow_create(args: Dict[str, Any]) -> str:
    desc = args.get("description") or args.get("task") or "nova tarefa"
    path = args.get("path") or args.get("file") or ""
    task = workflow.create_task(desc, path)
    return f"workflow criado {task.id[:8]} | {desc} -> {path} | stage={task.stage}"

def tool_workflow_analyze(args: Dict[str, Any]) -> str:
    path = args.get("path") or (workflow.get_current().file_path if workflow.get_current() else "")
    if not path:
        return "erro: path obrigatorio ou nenhuma tarefa ativa"
    fp = safe_path(str(path))
    if not fp.exists():
        return f"nao existe: {path}"
    current = workflow.get_current()
    if current:
        workflow.set_stage(current.id, "ANALISAR")
                                                  
    try:
        content = fp.read_text(encoding="utf-8", errors="ignore")
                                   
        if fp.suffix == ".py":
            import py_compile
            py_compile.compile(str(fp), doraise=True)
            analise = f"ANALISAR ok: {path} sintaxe valida, {len(content)} chars, {len(content.splitlines())} linhas"
        else:
            analise = f"ANALISAR ok: {path} existe, {len(content)} chars"
        if current:
            workflow.set_stage(current.id, "TESTAR")
        return analise + " [workflow: ANALISAR->TESTAR]"
    except Exception as e:
        err = f"ANALISAR erro {path}: {e}"
        if current:
            workflow.set_stage(current.id, "CORRIGIR", str(e))
        return err + " [workflow: ANALISAR->CORRIGIR]"

def tool_workflow_test(args: Dict[str, Any]) -> str:
    path = args.get("path") or (workflow.get_current().file_path if workflow.get_current() else "")
    cmd = args.get("command") or args.get("cmd") or ""
    if not path:
        return "erro: path obrigatorio"
    current = workflow.get_current()
    if current:
        workflow.set_stage(current.id, "TESTAR")

    fp = safe_path(str(path))
                                          
    if not cmd:
        if fp.suffix == ".py":
            cmd = f'python "{fp.name}"'
        elif fp.suffix == ".js":
            cmd = f'node "{fp.name}"'
        else:
            cmd = f'dir "{fp.name}"'

                               
    result = tool_shell({"command": cmd, "cwd": str(fp.parent)})
    if "exit=0" in result:
        msg = f"TESTAR ok {path}: {result[:2000]}"
        if current:
            workflow.set_stage(current.id, "APRESENTAR")
        return msg + " [workflow: TESTAR->APRESENTAR]"
    else:
        msg = f"TESTAR falhou {path}: {result[:3000]}"
        if current:
            workflow.set_stage(current.id, "CORRIGIR", result[:1000])
        return msg + " [workflow: TESTAR->CORRIGIR - precisa corrigir]"

def tool_workflow_fix(args: Dict[str, Any]) -> str:
                                                                                   
    error = args.get("error") or args.get("last_error") or ""
    current = workflow.get_current()
    if not current:
        return "nenhuma tarefa ativa"
    workflow.set_stage(current.id, "CORRIGIR", error)
    return f"workflow {current.id[:8]} em CORRIGIR | ultimo erro: {current.last_error[:500]} | use edit_file/write_file para corrigir e depois workflow_test"

def tool_workflow_present(args: Dict[str, Any]) -> str:
    current = workflow.get_current()
    if not current:
        return "nenhuma tarefa ativa"
    path = current.file_path or args.get("path") or ""
    workflow.set_stage(current.id, "APRESENTAR")
    out = []
    out.append(f"=== APRESENTACAO {current.id[:8]} ===")
    out.append(f"Arquivo: {path}")
    out.append(f"Descricao: {current.description}")
    out.append(f"Tentativas: {current.attempts}")
    if path:
        try:
            fp = safe_path(path)
            content = fp.read_text(encoding="utf-8", errors="ignore")
            out.append(f"--- CONTEUDO ({len(content)} chars) ---")
            out.append(content[:8000])
        except Exception as e:
            out.append(f"erro ao ler {path}: {e}")
    out.append(f"\nDiffs pendentes: {len(diff_tracker.entries)}")
                                  
    workflow.set_stage(current.id, "ABRIR")
    return "\n".join(out) + "\n[workflow: APRESENTAR->ABRIR]"

def tool_workflow_open(args: Dict[str, Any]) -> str:
    path = args.get("path") or (workflow.get_current().file_path if workflow.get_current() else "")
    if not path:
        return "erro: path obrigatorio"
    current = workflow.get_current()
    if current:
        workflow.set_stage(current.id, "CONCLUIDO")
        workflow.log(current.id, f"[ABRIR] codigo aberto para usuario: {path}")
        workflow.save()
    fp = safe_path(str(path))
    try:
        content = fp.read_text(encoding="utf-8", errors="ignore")
                                                     
        return f"<<<OPEN_FILE>>>{path}<<<END_OPEN>>>\n{content[:15000]}\n[workflow: ABRIR->CONCLUIDO] codigo aberto para usuario ver"
    except Exception as e:
        return f"erro ao abrir {path}: {e}"

def tool_workflow_list(_: Dict[str, Any]) -> str:
    return workflow.list_tasks()

def tool_workflow_status(_: Dict[str, Any]) -> str:
    cur = workflow.get_current()
    if not cur:
        return "nenhuma tarefa ativa\n" + workflow.list_tasks()
    return f"ATUAL: {cur.id[:8]} | {cur.stage} | {cur.file_path} | tentativas:{cur.attempts} | erro:{cur.last_error[:200]}\n---\n{workflow.list_tasks()}"

TOOLS: Dict[str, ToolFunc] = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "create_file": tool_write_file,
    "edit_file": tool_edit_file,
    "delete_file": tool_delete_file,
    "rename_file": tool_rename_file,
    "mkdir": tool_mkdir,
    "create_dir": tool_mkdir,
    "powershell": tool_shell,
    "bash": tool_shell,
    "terminal": tool_shell,
    "cmd": tool_shell,
    "shell": tool_shell,
    "grep": tool_grep,
    "grep_search": tool_grep,
    "code_search": tool_grep,
    "fetch": tool_fetch,
    "web_fetch": tool_fetch,
    "fetch_page": tool_fetch,
    "web_search": tool_search,
    "search": tool_search,
    "google": tool_search,
    "google_github": tool_google_github,
    "github_google": tool_google_github,
    "google_site_github": tool_google_github,
    "site_github_search": tool_google_github,
    "github_search": tool_google_github,
    "google_search": tool_google_github,
    "gsearch": tool_google_github,
    "google_fetch": tool_google_github,
    "site_search": tool_google_site,
    "google_site": tool_google_site,
    "google_site_search": tool_google_site,

    "workflow_create": tool_workflow_create,
    "workflow_analyze": tool_workflow_analyze,
    "workflow_analyse": tool_workflow_analyze,
    "workflow_test": tool_workflow_test,
    "workflow_fix": tool_workflow_fix,
    "workflow_present": tool_workflow_present,
    "workflow_open": tool_workflow_open,
    "workflow_list": tool_workflow_list,
    "workflow_status": tool_workflow_status,
    "present_code": tool_workflow_present,
    "open_code": tool_workflow_open,
}

TOOL_RE = re.compile(r"<<<TOOL_CALL>>>(.*?)<<<END_TOOL>>>", re.DOTALL)

def exec_tools(text: str) -> Tuple[str, List[str], List[str]]:
    import concurrent.futures
    calls = []
    for raw in TOOL_RE.findall(text):
        try:
            call = json.loads(raw.strip())
            calls.append(call)
        except json.JSONDecodeError as e:
            calls.append({"name": "__json_error__", "error": str(e), "raw": raw})

    def run_one(call: Dict[str, Any]) -> Tuple[str, str]:
        try:
            if call.get("name") == "__json_error__":
                return f"erro json tool: {call.get('error')}", "used json error..."
            name = call.get("name")
            args = call.get("args", {}) or {}
            if name not in TOOLS:
                return f"tool desconhecida: {name}", f"used {name}..."
            try:
                res = TOOLS[name](args)
            except Exception as e:
                res = f"erro tool {name}: {e}"
            q = args.get("query") or args.get("url") or args.get("path") or args.get("from") or args.get("command") or ""
            q = str(q)[:60]
            if name in ("grep", "grep_search", "code_search", "search_code", "fetch", "web_fetch", "fetch_page", "web_search", "search", "google", "google_github", "github_google", "google_search", "gsearch", "google_fetch", "site_search", "google_site", "google_site_search", "site_github_search", "github_search"):
                used_msg = f"fetched {q}..." if q else f"used {name}..."
            else:
                used_msg = f"used {name} {q}...".strip()
            return res, used_msg
        except Exception as e:
            return f"erro thread tool: {e}", f"used {call.get('name','?')} error..."

    results: List[str] = []
    used: List[str] = []
    if not calls:
        clean = TOOL_RE.sub("", text)
        clean = clean.replace("<<<TOOL_CALL>>>", "").replace("<<<END_TOOL>>>", "")
        clean = re.sub(r"<<<[^>]*>>>", "", clean)
        clean = clean.strip()
        return clean_output(clean), results, used

    with concurrent.futures.ThreadPoolExecutor(max_workers=5, thread_name_prefix="tool") as executor:
        futures = [executor.submit(run_one, call) for call in calls]
        for idx, future in enumerate(futures):
            call = calls[idx]
            try:
                res, used_msg = future.result(timeout=75)
                results.append(res)
                used.append(used_msg)
            except concurrent.futures.TimeoutError:
                results.append(f"erro timeout: tool {call.get('name')} travou apos 75s e foi abortada")
                used.append(f"used {call.get('name')} timeout...")
            except Exception as e:
                results.append(f"erro thread: {e}")
                used.append(f"used {call.get('name','?')} error...")

    clean = TOOL_RE.sub("", text)
    clean = clean.replace("<<<TOOL_CALL>>>", "").replace("<<<END_TOOL>>>", "")
    clean = re.sub(r"<<<[^>]*>>>", "", clean)
    clean = clean.strip()
    return clean_output(clean), results, used

                                                                             
                    
                                                                             
@dataclass
class Challenge:
    algorithm: str
    challenge: str
    salt: str
    signature: str
    target_path: str
    difficulty: float
    expire_at: int

class PowSolver:
    def __init__(self, wasm_path: Path):
        ensure_wasm()
        self.store = wasmtime.Store()
        mod = wasmtime.Module.from_file(self.store.engine, str(wasm_path))
        inst = wasmtime.Instance(self.store, mod, [])
        exp = inst.exports(self.store)
        self.mem = exp["memory"]
        self.solve_fn = exp["wasm_solve"]
        self.malloc = exp["__wbindgen_export_0"]
        self.stack_ptr = exp["__wbindgen_add_to_stack_pointer"]

    def _write_str(self, s: str) -> Tuple[int, int]:
        data = s.encode()
        ptr = self.malloc(self.store, len(data), 1)
                                                                                         
        try:
                                      
            self.mem.write(self.store, data, ptr)
        except Exception:
            try:
                base = self.mem.data_ptr(self.store)
                                     
                for i, b in enumerate(data):
                    base[ptr + i] = b
            except Exception:
                                             
                try:
                    view = self.mem.uint8_view(self.store)
                    view[ptr:ptr+len(data)] = data
                except Exception as e:
                    raise RuntimeError(f"falha ao escrever na memoria wasm: {e}")
        return ptr, len(data)

    def solve(self, chal: str, prefix: str, diff: float) -> Optional[int]:
        retptr = self.stack_ptr(self.store, -16)
        try:
            cp, cl = self._write_str(chal)
            pp, pl = self._write_str(prefix)
            self.solve_fn(self.store, retptr, cp, cl, pp, pl, float(diff))
                                                                                      
            try:
                             
                raw = self.mem.read(self.store, retptr, 16)
                if len(raw) < 16:
                    raise ValueError("read curto")
                status = struct.unpack("<i", raw[0:4])[0]
                value = struct.unpack("<d", raw[8:16])[0]
            except Exception:
                                             
                mem_ptr = self.mem.data_ptr(self.store)
                                                                       
                try:
                    s1 = bytes(mem_ptr[retptr:retptr+4])
                    s2 = bytes(mem_ptr[retptr+8:retptr+16])
                except Exception:
                                                   
                    view = self.mem.uint8_view(self.store)
                    s1 = bytes(view[retptr:retptr+4])
                    s2 = bytes(view[retptr+8:retptr+16])
                if len(s1) < 4 or len(s2) < 8:
                    raise RuntimeError(f"memoria wasm retornou buffer curto: {len(s1)}/{len(s2)} - wasm corrompido?")
                status = struct.unpack("<i", s1)[0]
                value = struct.unpack("<d", s2)[0]
        finally:
            try:
                self.stack_ptr(self.store, 16)
            except Exception:
                pass
        return None if status == 0 else int(value)

    def make_header(self, c: Dict[str, Any]) -> str:
                      
        challenge_obj = c
        ans = self.solve(
            challenge_obj["challenge"],
            f"{challenge_obj['salt']}_{challenge_obj['expire_at']}_",
            float(challenge_obj["difficulty"]),
        )
        if ans is None:
            raise RuntimeError("pow fail")
        payload = {
            "algorithm": challenge_obj["algorithm"],
            "challenge": challenge_obj["challenge"],
            "salt": challenge_obj["salt"],
            "answer": ans,
            "signature": challenge_obj["signature"],
            "target_path": challenge_obj["target_path"],
        }
        return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()

                                                                             
                    
                                                                             
class DeepSeekClient:
    def __init__(self, token: str, config: AppConfig):
        self.token = clean_token(token)
        self.config = config
        self.pow = PowSolver(config.wasm_path)
        self.lock = threading.Lock()
        self.http = httpx.Client(
            base_url=config.base_url,
            headers={
                "authorization": f"Bearer {self.token}",
                "content-type": "application/json",
                "user-agent": "Mozilla/5.0",
                "origin": config.base_url,
                "referer": f"{config.base_url}/",
            },
            timeout=httpx.Timeout(120.0, read=300.0),
        )
        self.sid: Optional[str] = None
        self.parent: Optional[int] = None

    def new_chat(self) -> str:
        r = self.http.post(self.config.session_path, json={})
        r.raise_for_status()
        try:
            d = r.json()
        except Exception as e:
            raise RuntimeError(f"resposta nao-json ao criar chat: {r.text[:500]} | erro {e}")

                                                                            
        data = d.get("data")
        if data is None:
                                                   
            msg = d.get("msg") or d.get("message") or d.get("error") or str(d)[:1000]
            raise RuntimeError(f"API retornou data=null (token expirado ou bloqueado?): {msg}")

                                                        
        biz = data.get("biz_data") if isinstance(data, dict) else None
        if biz is None:
                                            
            biz = data if isinstance(data, dict) else {}

        if not isinstance(biz, dict):
            raise RuntimeError(f"biz_data nao e dict: {biz} | full: {d}")

        sid = None
        cs = biz.get("chat_session")
        if isinstance(cs, dict):
            sid = cs.get("id") or cs.get("chat_session_id")
        elif isinstance(cs, str) and cs:
            sid = cs

        if not sid:
                                                           
            sid = (
                biz.get("id")
                or biz.get("chat_session_id")
                or biz.get("session_id")
                or (data.get("id") if isinstance(data, dict) else None)
                or (data.get("chat_session", {}).get("id") if isinstance(data.get("chat_session"), dict) else None)
            )

        if not sid:
            raise RuntimeError(f"sem sessao, resposta completa: {json.dumps(d, ensure_ascii=False)[:3000]}")

        self.sid = str(sid)
        self.parent = None
        return self.sid

    def pow_header(self) -> str:
        r = self.http.post(self.config.challenge_path, json={"target_path": self.config.completion_path})
        r.raise_for_status()
        try:
            j = r.json()
            data = j.get("data") or {}
            biz = data.get("biz_data") or data
            chal = biz.get("challenge") if isinstance(biz, dict) else None
            if chal is None:
                                                                     
                chal = j.get("data", {}).get("biz_data", {}).get("challenge") if isinstance(j.get("data"), dict) else None
            if chal is None:
                raise RuntimeError(f"challenge null: {j}")
        except Exception as e:
            raise RuntimeError(f"erro ao pegar challenge: {e} | resp: {r.text[:1000]}")
        with self.lock:
            return self.pow.make_header(chal)

    def stream(self, prompt: str):
        if not self.sid:
            self.new_chat()
        body: Dict[str, Any] = {
            "chat_session_id": self.sid,
            "parent_message_id": self.parent,
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": False,
            "search_enabled": False,
        }
        if self.parent is None:
            body["model_type"] = "default"

        meta: Dict[str, Any] = {}
        with self.http.stream(
            "POST", self.config.completion_path,
            json=body,
            headers={"x-ds-pow-response": self.pow_header()}
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                p = line[5:].strip()
                if not p or p == "[DONE]":
                    continue
                try:
                    obj = json.loads(p)
                except json.JSONDecodeError:
                    continue
                v = obj.get("v")
                if isinstance(v, dict) and "response" in v:
                    mid = v["response"].get("message_id")
                    if isinstance(mid, int):
                        meta["message_id"] = mid
                    for frag in v["response"].get("fragments", []):
                        if frag.get("type") == "RESPONSE" and frag.get("content"):
                            yield frag["content"]
                    continue
                if "p" in obj:
                    if obj["p"].endswith("message_id") and isinstance(v, int):
                        meta["message_id"] = v
                    if obj.get("o") == "APPEND" and isinstance(v, str) and obj["p"].endswith("content"):
                        yield v
                    continue
                if isinstance(v, str):
                    yield v

        if meta.get("message_id"):
            self.parent = meta["message_id"]

    def close(self):
        try:
            self.http.close()
        except Exception:
            pass

                                                                             
                          
                                                                             
DH_ART = r"""
                **gggrgM**M#mggg**
                **wgNN@"B*P""mp""@d#"@N#Nw**
              *g#@0F*a*F#  **F9m* ,F9*__9NG#g_
           *mN#F  aM"    #p"    !q@    9NL "9#Qu*
          g#MF *pP"L*  g@"9L_  *g""#*_  g"9w_ 0N#p
        *0F jL*"   7*wF     #_gF     9gjF   "bJ  9h_
       j#  gAF    *@NL*     g@#_      J@u_    2#_  #_
      ,FF_#" 9_ *#"  "b*  g@   "hg  *#"  !q* jF "*_09_
      F N"    #p"      Ng@       `#g"      "w@    "# t
     j p#    g"9_     g@"9_      gP"#_     gF"q    Pb L
     0J  k *@   9g* j#"   "b_  j#"   "b_ *d"   q* g  ##
     #F  `NF     "#g"       "Md"       5N#      9W"  j#
     #k  jFb_    g@"q_     _*"9m_     _*"R_    _#Np  J#
     tApjF  9g  J"   9M_ _m"    9%_ **"   "#  gF  9*jNF
      k`N    "q#       9g@        #gF       ##"    #"j
      `_0q_   #"q_    _&"9p_    _g"`L_    _*"#   jAF,'
       9# "b_j   "b_ g"    *g gF    9 g#"  "L_*"qNF
        "b_ "#_    "NL      *B#*      I@     j#" _#"
          NM_0"*g_ j""9u_  gP  q_  _w@ ]_ *g*"F*g@
           "NNh_ !w#_   9#g"    "m*"   *#*"* dN@"
              9##g_0@q__ #"4_  j*"k __*NF_g#@P"
                "9NN#gIPNL_ "b@" _2M"Lg#N@F"
                    ""P@*NN#gEZgNN@#@P""
"""

MUDIN_PROMPT = """Voce e kernel11-chat, agente autonomo estilo Claude Code. Criado por Mudin. Voce e 100% AUTONOMO com PERSISTENCIA.

IDENTIDADE:
- Nome: kernel11-chat Autonomous chat
- Voce resolve sozinho, nao pede permissao pra buscar, nao pergunta "quer que eu busque?"

REGRAS DE AUTONOMIA - OBRIGATORIO:
1. NUNCA pergunte se pode buscar. SE PRECISA DE DADO EXTERNO, BUSQUE DIRETO.
2. Se usuario falar de produto, preco, hardware, DDR3, etc: faca web_search + fetch IMEDIATAMENTE em paralelo.
3. Use MULTIPLOS TOOL_CALLS por resposta. Ex: 3-5 buscas/fetchs de uma vez.
4. Fluxo preco: web_search "DDR3 8GB preco menor" + fetch em ML, Terabyte, Buscape, Zoom -> compare e entregue precos reais.
5. Se falhar um fetch, tente bypass e proxima loja.
6. Saudacao ("opa", "oi") = CURTA "fala" + pergunta o que precisa. Sem list_files.
7. Resposta limpa, direta, sem **, sem enrolacao. Sempre com DADO REAL quando for busca.
8. PROIBIDO: "Quer que eu busque?", "Posso procurar?". Voce BUSCA e entrega.
9. Quando terminar buscas, resuma: menor preco, media, onde comprar, link.
10. OFERTA DE NAVEGADOR: Quando encontrar algo interessante, SEMPRE finalize com: "quer que eu abra no seu navegador os resultados?"

REGRAS DE BUSCA - QUAL TOOL USAR:
- CODIGO/SOURCE/DOCUMENTACAO/API/LIB/BIBLIOTECA: SEMPRE use grep (grep.app) PRIMEIRO. Ex: {"query":"mta server query EYE1 parser"} - grep busca direto no codigo do GitHub, muito melhor que Google pra source.
- DOCUMENTACAO OFICIAL (python docs, mdn, etc): use fetch direto na URL da doc + grep se precisar de exemplo de codigo
- PRECO/PRODUTO/LOJA/NOTICIA/GERAL: use web_search (DuckDuckGo) + google_search/site_search + fetch nas lojas
- GITHUB mas quer discussao/issues: use google_search com site:github.com
- NUNCA use google pra buscar codigo fonte puro - grep e 10x melhor. Google so pra resto.
- Exemplo correto:
  Usuario: "como funciona EYE1 no MTA?" -> grep {"query":"EYE1 MTA server browser"} (nao google)
  Usuario: "preco DDR3" -> web_search + google_search site:terabyteshop.com.br (nao grep)

WORKFLOW OBRIGATORIO COM PERSISTENCIA - 3 ETAPAS (CRIAR -> ANALISAR/TESTAR -> APRESENTAR/ABRIR):
TODA vez que for criar codigo/projeto, SIGA RIGOROSAMENTE:

ETAPA 1 - CRIAR (com persistencia):
- Use workflow_create {"description":"o que vai fazer", "path":"workspace/arquivo.py"}
- Depois write_file com o codigo
- O sistema ja persiste em workspace/.kernel11_workflow.json

ETAPA 2 - ANALISAR -> TESTAR:
- Use workflow_analyze {"path":"workspace/arquivo.py"} -> verifica sintaxe
- Use workflow_test {"path":"workspace/arquivo.py", "command":"python arquivo.py"} -> testa de verdade via powershell
- Se der erro, vai para CORRIGIR

ETAPA 2b - CORRIGIR (se erro):
- Use workflow_fix + edit_file/write_file para corrigir
- Teste novamente com workflow_test
- Repita ate funcionar (max 5 tentativas)

ETAPA 3 - APRESENTAR -> ABRIR:
- Se funcionar: workflow_present -> mostra codigo e diffs
- Depois workflow_open -> abre codigo para usuario ver (marca CONCLUIDO)
- Finalize com resumo + "quer que eu abra no seu navegador os resultados?" se for busca, ou mostre que arquivo esta pronto

PERSISTENCIA:
- Tudo salvo em workspace/.kernel11_workflow.json
- Use workflow_list e workflow_status para ver tarefas
- Se o programa fechar, ao voltar ele continua de onde parou (le o .json)

EXEMPLO COMPLETO DE CODIGO:
Usuario: "cria um bot de precos DDR3"
Voce faz:
<<<TOOL_CALL>>>{"name":"workflow_create","args":{"description":"bot precos DDR3","path":"workspace/bot_ddr3.py"}}<<<END_TOOL>>>
<<<TOOL_CALL>>>{"name":"write_file","args":{"path":"workspace/bot_ddr3.py","content":"codigo..."}}<<<END_TOOL>>>
<<<TOOL_CALL>>>{"name":"workflow_analyze","args":{"path":"workspace/bot_ddr3.py"}}<<<END_TOOL>>>
<<<TOOL_CALL>>>{"name":"workflow_test","args":{"path":"workspace/bot_ddr3.py","command":"python bot_ddr3.py"}}<<<END_TOOL>>>
Se erro:
<<<TOOL_CALL>>>{"name":"edit_file","args":{"path":"workspace/bot_ddr3.py","old_text":"...","new_text":"..."}}<<<END_TOOL>>>
<<<TOOL_CALL>>>{"name":"workflow_test","args":{"path":"workspace/bot_ddr3.py"}}<<<END_TOOL>>>
Se ok:
<<<TOOL_CALL>>>{"name":"workflow_present","args":{"path":"workspace/bot_ddr3.py"}}<<<END_TOOL>>>
<<<TOOL_CALL>>>{"name":"workflow_open","args":{"path":"workspace/bot_ddr3.py"}}<<<END_TOOL>>>

Tools disponiveis:
- list_files, read_file, write_file, edit_file, delete_file, rename_file, mkdir
- powershell/bash/terminal (roda comandos)
- grep, fetch, web_search
- google_search/gsearch (Google generico com QUALQUER site: - ex: {"query":"site:github.com intext:mta parser"} ou {"query":"site:terabyteshop.com.br DDR3 8GB"} ou {"query":"site:stackoverflow.com python error"})
- site_search/google_site (com site separado - ex: {"site":"github.com","query":"mta parser"} ou {"site":"reddit.com","query":"best ddr3"})
- workflow_create, workflow_analyze, workflow_test, workflow_fix, workflow_present, workflow_open, workflow_list, workflow_status

Formato tool (varios por mensagem permitido):
<<<TOOL_CALL>>>
{"name":"write_file","args":{"path":"workspace/main.py","content":"..."}}
<<<END_TOOL>>>

Workspace atual:
__FILES__
"""

def print_header():
    art = DH_ART.strip("\n")
    for line in art.splitlines():
        print(line)
    print(f"{SYM['box_tl']}{SYM['box_h']*52}{SYM['box_tr']}")
    print(f"{SYM['box_v']}  {SYM['star']} {Colors.bold('kernel11-chat')} Autonomous chat{' '*18}{SYM['box_v']}")
    print(f"{SYM['box_v']}  {Colors.gray('workspace/ - powershell - web - grep.app')}       {SYM['box_v']}")
    print(f"{SYM['box_bl']}{SYM['box_h']*52}{SYM['box_br']}")

def thinking_anim(stop_event: threading.Event):
    frames = [SYM['dot'], SYM['star'], SYM['star2'], SYM['star3']]
    i = 0
    while not stop_event.is_set():
        ch = frames[i % len(frames)]
        sys.stdout.write(f"\r  {ch} Thinking...  ")
        sys.stdout.flush()
        i += 1
        time.sleep(0.12)
    sys.stdout.write("\r" + " " * 30 + "\r")
    sys.stdout.flush()

def enter_listener(interrupt_event: threading.Event, stop_event: threading.Event):
    try:
        if os.name == "nt":
            import msvcrt
            while not stop_event.is_set() and not interrupt_event.is_set():
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in (b'\r', b'\n', b'\x03'):
                        interrupt_event.set()
                        break
                time.sleep(0.05)
        else:
            if not sys.stdin.isatty():
                return
            import select, termios, tty
            old = None
            try:
                old = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
            except Exception:
                old = None
            try:
                while not stop_event.is_set() and not interrupt_event.is_set():
                    r, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r:
                        ch = sys.stdin.read(1)
                        if ch in ('\n', '\r', '\x03'):
                            interrupt_event.set()
                            break
            finally:
                if old:
                    try:
                        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
                    except Exception:
                        pass
    except Exception:
        pass

                                                                             
              
                                                                             
def main():
    storage = JsonStorage(CONFIG.json_path)
    data = storage.load()

    token = os.getenv("DEEPSEEK_TOKEN") or data.get("usertoken") or ""
    token = clean_token(token)
    if not token:
        token = clean_token(input("token: ").strip())
    if len(token) < 50:
        print("token invalido (muito curto)")
        return
    data["usertoken"] = token
    storage.save(data)

    client = DeepSeekClient(token, CONFIG)
    try:
        client.new_chat()
    except Exception as e:
        print(f"{Colors.red('erro ao criar chat')}: {e}")
        return

    print_header()
    print(Colors.gray(f"workspace {CONFIG.workspace.resolve()} | chat {client.sid[:8]}"))

    try:
        while True:
            try:
                q = input(f"> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not q:
                continue
            if q in ("/sair", "exit", "quit"):
                break
            if q == "/reset":
                try:
                    client.new_chat()
                    print(Colors.gray(f"new chat {client.sid[:8]}"))
                except Exception as e:
                    print(Colors.red(f"erro reset: {e}"))
                continue
            if q in ("/ls", "ls"):
                print(workspace_mgr.list_files())
                continue

            data["history"].append({"role": "user", "content": q, "time": datetime.datetime.now().isoformat()})
            storage.save(data)

            prompt = MUDIN_PROMPT.replace("__FILES__", workspace_mgr.list_files()) + f"\n\nUsuario: {q}\n"
            start_time = time.time()
            interrupted = False

                                                                                         
            print(f"  {Colors.gray(SYM['corner'] + ' Press Enter to stop')}")

            for loop in range(15):
                stop_evt = threading.Event()
                interrupt_evt = threading.Event()
                anim_t = threading.Thread(target=thinking_anim, args=(stop_evt,), daemon=True)
                anim_t.start()
                enter_t = threading.Thread(target=enter_listener, args=(interrupt_evt, stop_evt), daemon=True)
                enter_t.start()

                buf: List[str] = []
                first_chunk = True

                try:
                    for chunk in client.stream(prompt):
                        if interrupt_evt.is_set():
                            interrupted = True
                            break
                        if first_chunk:
                            stop_evt.set()
                            anim_t.join(timeout=0.5)
                            first_chunk = False
                            print()
                        buf.append(chunk)
                except Exception as e:
                    if interrupt_evt.is_set():
                        interrupted = True
                    else:
                        stop_evt.set()
                        anim_t.join(timeout=0.5)
                        print(Colors.red(f"erro stream: {e}"))
                        break
                finally:
                    stop_evt.set()
                    try:
                        anim_t.join(timeout=0.5)
                    except Exception:
                        pass

                if first_chunk:
                    stop_evt.set()
                    try:
                        anim_t.join(timeout=0.5)
                    except Exception:
                        pass

                if interrupted:
                    print(f"\n  {Colors.red('! Response stopped by user')}")
                    break

                raw = "".join(buf)
                clean, results, used = exec_tools(raw)

                if clean:
                    print(f"\n{clean}\n")

                auto_workflow_results = []
                try:
                    cur = workflow.get_current()
                    if cur:
                        if cur.stage == "TESTAR" and cur.file_path:
                            print(f"  {SYM['bullet']} auto workflow_test {cur.file_path}...")
                            auto_res = tool_workflow_test({"path": cur.file_path})
                            auto_workflow_results.append(auto_res)
                            results.append(auto_res)
                            used.append(f"used workflow_test {cur.file_path} auto...")
                        elif cur.stage == "ANALISAR" and cur.file_path:
                            print(f"  {SYM['bullet']} auto workflow_analyze {cur.file_path}...")
                            auto_res = tool_workflow_analyze({"path": cur.file_path})
                            auto_workflow_results.append(auto_res)
                            results.append(auto_res)
                            used.append(f"used workflow_analyze {cur.file_path} auto...")
                        elif cur.stage == "APRESENTAR":
                            print(f"  {SYM['bullet']} auto workflow_present...")
                            auto_res = tool_workflow_present({})
                            auto_workflow_results.append(auto_res)
                            results.append(auto_res)
                            used.append("used workflow_present auto...")
                        elif cur.stage == "ABRIR" and cur.file_path:
                            print(f"  {SYM['bullet']} auto workflow_open {cur.file_path}...")
                            auto_res = tool_workflow_open({"path": cur.file_path})
                            auto_workflow_results.append(auto_res)
                            results.append(auto_res)
                            used.append(f"used workflow_open {cur.file_path} auto...")
                except Exception as e:
                    results.append(f"erro auto workflow: {e}")

                if not used:
                    data["history"].append({"role": "assistant", "content": clean, "time": datetime.datetime.now().isoformat()})
                    storage.save(data)
                    break

                for u in used:
                    if u.startswith("fetched"):
                        print(f"  {SYM['corner']} {Colors.gray(u)}")
                    else:
                        print(f"  {SYM['bullet']} {u}")

                if auto_workflow_results:
                    prompt = "Resultados (incluindo auto workflow):\n" + "\n".join(results) + f"\nArquivos:\n{workspace_mgr.list_files()}\nContinue o workflow ate CONCLUIDO."
                else:
                    prompt = "Resultados:\n" + "\n".join(results) + f"\nArquivos:\n{workspace_mgr.list_files()}\nContinue ou finalize."

            diff_tracker.show_and_clear()
            elapsed = time.time() - start_time
            if interrupted:
                print(f"\n  {SYM['claude_dot']} {Colors.gray(f'Stopped - {elapsed:.1f}s')}")
            else:
                print(f"\n  {SYM['claude_dot']} {Colors.gray(f'{elapsed:.1f}s')}")

    finally:
        client.close()

if __name__ == "__main__":
    main()
