"""Fixtures for the hermetic skill-execution-report test suite.

Everything here builds throw-away SQLite databases, skill directories and
usage files under a temporary directory. No test reads the real Hermes state,
touches the network, or depends on the developer's configuration.

Importing this module has two deliberate, process-wide side effects:

* ``SCRIPTS_DIR`` is prepended to ``sys.path`` so the tests can ``import
  report``/``evidence``/``classify`` without installing the skill. The insert
  is intentional, guarded (it is skipped when the path is already present) and
  scoped to the interpreter running the suite: nothing is written to disk and
  nothing outside this process is affected.
* Streams and environment are never patched at import time. ``report.main()``
  reads ``HERMES_HOME`` and ``SER_NOW`` when it runs, so :func:`run_cli` can
  point a single call at a sandbox, capture the streams and then restore the
  previous values.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile

SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
# Intencional e de escopo de processo: permite ``import report`` sem instalar a
# skill. A guarda ``not in sys.path`` evita duplicar a entrada, de modo que
# reimportar este módulo não muda o ``sys.path`` além da primeira vez.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

#: Column definitions mirroring the Hermes state.db schema the tool expects.
SESSION_COLUMNS = (
    "id TEXT PRIMARY KEY",
    "source TEXT",
    "title TEXT",
    "started_at REAL",
    "ended_at REAL",
    "message_count INTEGER",
    "tool_call_count INTEGER",
    "parent_session_id TEXT",
    "cwd TEXT",
    "git_repo_root TEXT",
    "git_branch TEXT",
    "hidden INTEGER DEFAULT 0",
    "archived INTEGER DEFAULT 0",
    "estimated_cost_usd REAL",
    "actual_cost_usd REAL",
    "cost_status TEXT",
    "input_tokens INTEGER",
    "output_tokens INTEGER",
)
MESSAGE_COLUMNS = (
    "id INTEGER PRIMARY KEY",
    "session_id TEXT",
    "role TEXT",
    "content TEXT",
    "tool_call_id TEXT",
    "tool_calls TEXT",
    "tool_name TEXT",
    "timestamp REAL",
    "active INTEGER DEFAULT 1",
    "compacted INTEGER DEFAULT 0",
    "message_uid TEXT",
    "display_kind TEXT",
)


def tool_calls(*calls) -> str:
    """Stored ``tool_calls`` JSON for ``(name, args, call_id)`` tuples."""
    payload = []
    for name, args, call_id in calls:
        payload.append({
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
    return json.dumps(payload)


def tool_result(**fields) -> str:
    """A ``role=tool`` body as stored by Hermes."""
    return json.dumps(fields)


def assistant(uid: str, ts: float, calls=(), content: str = "", **extra) -> dict:
    row = {
        "role": "assistant",
        "message_uid": uid,
        "timestamp": ts,
        "content": content,
        "tool_calls": tool_calls(*calls),
    }
    row.update(extra)
    return row


def tool(uid: str, ts: float, call_id: str, body: str,
         name: str = "terminal", **extra) -> dict:
    row = {
        "role": "tool",
        "message_uid": uid,
        "timestamp": ts,
        "tool_call_id": call_id,
        "tool_name": name,
        "content": body,
    }
    row.update(extra)
    return row


def user(uid: str, ts: float, content: str, **extra) -> dict:
    row = {"role": "user", "message_uid": uid, "timestamp": ts, "content": content}
    row.update(extra)
    return row


def session(sid: str, **kw) -> dict:
    row = {
        "id": sid,
        "source": "cli",
        "title": "Sessão %s" % sid,
        "started_at": 1000.0,
        "ended_at": None,
        "message_count": 0,
        "tool_call_count": 0,
        "parent_session_id": None,
        "cwd": None,
        "git_repo_root": None,
        "git_branch": None,
        "hidden": 0,
        "archived": 0,
        "estimated_cost_usd": None,
        "actual_cost_usd": None,
        "cost_status": None,
        "input_tokens": None,
        "output_tokens": None,
    }
    row.update(kw)
    return row


class Sandbox:
    """A temporary ``HERMES_HOME`` populated with synthetic sources.

    Use it as a context manager (``with Sandbox() as box:``) in probes and in
    helpers that are not a ``TestCase``; the temporary tree is then released
    even when the body raises. Inside a ``TestCase`` prefer ``cleanup()``
    through ``addCleanup``, which runs after the test whatever its outcome.
    """

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ser-test-", ignore_cleanup_errors=True)
        self.root = pathlib.Path(self._tmp.name)
        self.home = self.root / "home"
        (self.home / "skills").mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "state.db"
        self.usage_path = self.home / "skills" / ".usage.json"

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        # Nunca engole a exceção do bloco: apenas libera a árvore temporária.
        self.cleanup()
        return False

    def cleanup(self) -> None:
        """Libera a árvore temporária. Idempotente: chamar duas vezes é seguro."""
        self._tmp.cleanup()

    # ------------------------------------------------------------- sources
    def skill(self, name: str) -> pathlib.Path:
        target = self.home / "skills" / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(
            "---\nname: %s\n---\n" % name, encoding="utf-8")
        return target

    def usage(self, data) -> pathlib.Path:
        self.usage_path.write_text(json.dumps(data), encoding="utf-8")
        return self.usage_path

    def database(self, sessions=(), messages=(), drop=(), raw=None, tables=None):
        """Create (or rebuild) ``state.db``. ``drop`` removes columns; ``raw`` writes bytes.

        Idempotente: cada chamada faz ``DROP TABLE IF EXISTS`` nas tabelas
        declaradas antes de recriá-las, então um teste pode chamar o método de
        novo (para acrescentar uma segunda fonte, uma coluna nova, outra leva de
        linhas) em vez de falhar com ``table already exists``. A reconstrução
        atinge apenas o ``state.db`` deste sandbox.

        A exceção é ``raw``: ele grava bytes crus, que não são um SQLite
        abrível, de modo que uma chamada seguinte sem ``raw`` sobrescreve o
        arquivo por inteiro em vez de executar os ``DROP TABLE``.

        Só *nomes* (tabela e coluna) são interpolados na DDL/DML, e todos vêm
        das constantes deste módulo ou de argumentos explícitos do teste --
        nunca de dados de origem. Todo *valor* de linha passa por placeholders
        ``?`` em :meth:`_insert`, de modo que uma fixture pode guardar aspas,
        ``%`` ou palavras-chave SQL literalmente sem alterar o comando.
        """
        if raw is not None:
            self.db_path.write_bytes(raw)
            return self.db_path
        conn = sqlite3.connect(str(self.db_path))
        try:
            definition = tables or {
                "sessions": SESSION_COLUMNS,
                "messages": MESSAGE_COLUMNS,
            }
            for table, columns in definition.items():
                conn.execute("DROP TABLE IF EXISTS %s" % table)
                kept = [c for c in columns if c.split()[0] not in drop]
                conn.execute("CREATE TABLE %s (%s)" % (table, ", ".join(kept)))
            self._insert(conn, "sessions", sessions)
            self._insert(conn, "messages", messages)
            conn.commit()
        finally:
            conn.close()
        return self.db_path

    @staticmethod
    def _insert(conn, table, rows) -> None:
        for row in rows:
            keys = sorted(row)
            conn.execute(
                "INSERT INTO %s (%s) VALUES (%s)" % (
                    table, ", ".join(keys), ", ".join("?" * len(keys))),
                [row[key] for key in keys],
            )

    def dir(self, *parts) -> pathlib.Path:
        target = self.root.joinpath(*parts)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def file(self, relative: str, text: str = "conteúdo") -> pathlib.Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    # ----------------------------------------------------------- execution
    def run(self, argv=(), now: str = "2026-10-13T12:00:00Z"):
        return run_cli(argv, self.home, now=now)


def run_cli(argv, home, now: str = "2026-10-13T12:00:00Z"):
    """Run the CLI in-process against ``home`` and capture its streams.

    Contract (achado 42): ``report.main()`` lê ``HERMES_HOME`` e ``SER_NOW`` do
    ambiente *no momento em que é chamado* -- nunca no import. Por isso este
    helper escreve as duas variáveis imediatamente antes da chamada e devolve os
    valores anteriores em ``finally``; apontar ``home`` para outro sandbox no
    meio da suíte não exige reimportar nada.

    Os streams vêm de ``contextlib.redirect_stdout``/``redirect_stderr``, que
    restauram sozinhos mesmo quando ``main`` levanta. O restante (ambiente) é
    restaurado no ``finally``, que também roda nesse caminho: nenhum teste deixa
    ``HERMES_HOME``/``SER_NOW`` alterados para o próximo, e ``run_cli`` não
    substitui ``sys.stdout``/``sys.stderr`` de forma permanente (achado 14).

    ``argv`` é sempre um iterável de argumentos, como ``("--json",)``.
    Devolve ``(exit_code, stdout, stderr)``.
    """
    import report

    previous = {key: os.environ.get(key) for key in ("HERMES_HOME", "SER_NOW")}
    os.environ["HERMES_HOME"] = str(home)
    os.environ["SER_NOW"] = now
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = report.main(list(argv))
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return code, out.getvalue(), err.getvalue()


def findings_of(payload: dict, scope: str = "main") -> list:
    return [item for item in payload["findings"] if item["scope"] == scope]


def finding_for(payload: dict, name: str, scope: str = "main"):
    for item in payload["findings"]:
        if item["skill"] == name and item["scope"] == scope:
            return item
    return None


def find_one(payload: dict, name: str, scope: str = "main") -> dict:
    """Como :func:`finding_for`, mas falha alto quando o achado não existe.

    Mesma assinatura de :func:`finding_for` -- ``(payload, name, scope="main")``
    -- para que um teste possa alternar entre a forma leniente e a estrita sem
    mudar a chamada (achado 79). Os helpers de busca da suíte usam essa
    assinatura; nenhum deles recebe o payload em outra ordem.
    """
    found = finding_for(payload, name, scope)
    if found is None:
        raise AssertionError("achado ausente para %r (scope=%s)" % (name, scope))
    return found


def compacted(uid: str, ts: float, content: str = "trecho já compactado", **extra) -> dict:
    """Uma linha de transcript que o Hermes já compactou (``compacted = 1``).

    A compactação reescreve o conteúdo da linha, mas o relatório precisa
    continuar analisando a mensagem e declarar a limitação, em vez de perder a
    evidência silenciosamente (achado 76).
    """
    row = user(uid, ts, content, **extra)
    row["compacted"] = 1
    return row
