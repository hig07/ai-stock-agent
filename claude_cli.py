import os
import sys
import json
import shutil
import subprocess

# ==========================================
# 🤖 Claude 구독(Claude Code CLI) 헤드리스 호출 공용 모듈
#   claude -p 로 한 번 실행하고 결과를 돌려준다. (API 키 불필요, 구독 사용량 사용)
# ==========================================

CLAUDE_TIMEOUT = 180  # 초


class UsageLimitReached(Exception):
    """Claude 구독 사용 한도 도달 - 더 돌려도 실패하므로 호출을 멈춘다."""


def find_claude_exe():
    # 환경변수 CLAUDE_BIN으로 직접 지정 가능 (설치 위치가 특이한 경우)
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    # npm 설치본은 claude.cmd 래퍼라서, 인자 전달이 안전한 실제 claude.exe를 찾아서 쓴다
    cmd_path = shutil.which("claude") or os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")
    exe = os.path.join(os.path.dirname(cmd_path), "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
    if os.path.exists(exe):
        return exe
    if os.path.exists(cmd_path):
        return cmd_path
    print("🚨 claude CLI를 찾을 수 없습니다. Claude Code가 설치되어 있는지 확인하세요.")
    sys.exit(1)


CLAUDE_EXE = find_claude_exe()


def run_claude(prompt_text, system_prompt, model="haiku", json_schema=None):
    """json_schema를 주면 그 형식의 dict를, 없으면 답변 텍스트를 돌려준다."""
    cmd = [
        CLAUDE_EXE, "-p",
        "--model", model,
        "--tools", "",                 # 도구 없이 답변만
        "--strict-mcp-config",         # MCP 서버 로딩 생략
        "--no-session-persistence",    # 호출 기록은 세션에 남기지 않음
        "--output-format", "json",
        "--system-prompt", system_prompt,
    ]
    if json_schema:
        cmd += ["--json-schema", json.dumps(json_schema, ensure_ascii=False)]

    proc = subprocess.run(cmd, input=prompt_text.encode("utf-8"), capture_output=True, timeout=CLAUDE_TIMEOUT)
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        message = (stderr or stdout).strip()
        if "limit" in message.lower():
            raise UsageLimitReached(message)
        raise RuntimeError(f"Claude 응답 파싱 실패 (exit={proc.returncode}): {message[:300]}")

    if result.get("is_error"):
        message = str(result.get("result", ""))
        if result.get("api_error_status") == 429 or "limit" in message.lower():
            raise UsageLimitReached(message)
        raise RuntimeError(f"Claude 오류: {message[:300]}")

    if json_schema:
        data = result.get("structured_output")
        if not data:
            raise RuntimeError("Claude가 구조화된 결과를 돌려주지 않았습니다.")
        return data
    return result.get("result", "")
