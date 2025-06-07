from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import asyncio
import os
import pty
import subprocess
import sys
import openai
from dotenv import load_dotenv
import re
import json
import io
import contextlib

load_dotenv() # .env 파일 로드

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 시 전체 허용, 배포 시 도메인 제한 권장
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 서버 메모리 내 파일 관리 (간단한 dict 사용)
files_db = {
    "main.py": "print('Hello, world!')",
    "test.py": "print('Test file')"
}

@app.get("/ping")
def ping():
    return {"message": "pong"}

# 파일 목록 반환
@app.get("/files")
def list_files():
    return {"files": list(files_db.keys())}

# 파일 내용 읽기
@app.get("/files/{filename}")
def read_file(filename: str):
    if filename not in files_db:
        raise HTTPException(status_code=404, detail="파일이 존재하지 않습니다.")
    return {"filename": filename, "content": files_db[filename]}

# 파일 생성
class CreateFileRequest(BaseModel):
    filename: str
    content: str = ""

@app.post("/files")
def create_file(req: CreateFileRequest):
    if req.filename in files_db:
        raise HTTPException(status_code=400, detail="이미 존재하는 파일입니다.")
    files_db[req.filename] = req.content
    return {"message": "파일 생성 완료", "filename": req.filename}

# 파일 내용 수정
class UpdateFileRequest(BaseModel):
    content: str

@app.put("/files/{filename}")
def update_file(filename: str, req: UpdateFileRequest):
    if filename not in files_db:
        raise HTTPException(status_code=404, detail="파일이 존재하지 않습니다.")
    files_db[filename] = req.content
    return {"message": "파일 수정 완료", "filename": filename}

# 파일 삭제
@app.delete("/files/{filename}")
def delete_file(filename: str):
    if filename not in files_db:
        raise HTTPException(status_code=404, detail="파일이 존재하지 않습니다.")
    del files_db[filename]
    return {"message": "파일 삭제 완료", "filename": filename}

# 코드 실행 요청용
class CodeRequest(BaseModel):
    code: str

@app.post("/run")
def run_code(req: CodeRequest):
    # TODO: 실제 코드 실행 구현
    # return {"output": "실행 결과 예시"}
    
    # Python 코드 실행을 위해 io.StringIO를 사용하여 stdout/stderr를 캡처합니다.
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    redirected_output = io.StringIO()
    redirected_error = io.StringIO()
    sys.stdout = redirected_output
    sys.stderr = redirected_error

    try:
        # exec() 함수로 코드 실행
        # globals와 locals를 {}로 설정하여 실행 환경을 격리합니다.
        exec(req.code, {}, {})
        output = redirected_output.getvalue()
        error_output = redirected_error.getvalue()
        
        if error_output:
            return {"output": error_output, "status": "error"}
        else:
            return {"output": output, "status": "success"}
    except Exception as e:
        return {"output": str(e), "status": "error"}
    finally:
        # stdout/stderr를 원래대로 복원
        sys.stdout = old_stdout
        sys.stderr = old_stderr

# AI 채팅 요청용
class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
def chat_with_ai(req: ChatRequest):
    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "**매우 중요: 응답은 오직 JSON 객체 문자열이어야 합니다. 마크다운 코드 블록(```json)으로 감싸지 마세요.** "
                        "당신은 코딩 제안을 제공하는 유용한 도우미입니다. 사용자의 언어로 응답하세요. "
                        "당신의 주요 목표는 사용자가 코드를 작성하는 데 도움을 주는 것입니다. "
                        "사용자의 요청이 구체적이지 않다면, 가장 일반적이고 간단한 코드(예: HTML, CSS, JavaScript)를 먼저 제안하세요. "
                        "만약 코드 제안이 어렵다면, 코드를 생성하는 데 필요한 1-2가지의 가장 핵심적인 질문만 하세요. "
                        "당신의 **모든 응답은 JSON 객체여야 합니다.** JSON 객체는 항상 두 개의 최상위 키를 포함해야 합니다: "
                        "`code_suggestions`와 `explanation`. `code_suggestions`는 {filename: string, content: string} 객체의 배열입니다. "
                        "코드 제안이 없는 경우에도 `code_suggestions`는 빈 배열(`[]`)로 제공되어야 합니다. "
                        "`explanation` 키는 코드를 설명하거나 질문을 하는 자연어만 포함해야 하며, JSON이나 코드 블록을 포함해서는 안 됩니다. "
                        "질문을 해야 할 경우, 질문은 `explanation` 필드 안에 번호 매기기 목록 형식으로 포함하세요."
                    )
                },
                {"role": "user", "content": req.message}
            ]
        )
        
        ai_response_content = completion.choices[0].message.content
        print(f"Raw AI response content: {ai_response_content}")
        
        # Try to remove markdown code block wrapper if present
        json_string_to_parse = ai_response_content.strip()
        if json_string_to_parse.startswith("```json") and json_string_to_parse.endswith("```"):
            json_string_to_parse = json_string_to_parse[len("```json"):].strip()
            if json_string_to_parse.endswith("```"):
                json_string_to_parse = json_string_to_parse[:-len("```")].strip()

        print(f"Prepared JSON string for parsing: {json_string_to_parse}")
        
        try:
            parsed_response = json.loads(json_string_to_parse)
            explanation = parsed_response.get("explanation", "")
            code_suggestions = parsed_response.get("code_suggestions", [])
        except json.JSONDecodeError as e:
            print(f"JSON decoding error: {e}")
            # If decoding fails, the explanation should indicate there was a parsing issue.
            # We don't want to show the raw, invalid JSON to the user as "explanation".
            explanation = f"AI 응답을 파싱하는 데 문제가 발생했습니다. 원시 응답: {ai_response_content}. 오류: {e}"
            code_suggestions = []

        return {"response": {"explanation": explanation, "code_suggestions": code_suggestions}}
    except Exception as e:
        print(f"An unexpected error occurred in chat_with_ai: {e}")
        raise HTTPException(status_code=500, detail=f"서버 오류: {e}")

# DSL 파싱 요청용
class DSLRequest(BaseModel):
    dsl_code: str

@app.post("/parse-dsl")
def parse_dsl(req: DSLRequest):
    # TODO: DSL 파싱 구현
    return {"result": "DSL 파싱 결과 예시"}

class RenameFileRequest(BaseModel):
    new_filename: str

@app.patch("/files/{filename}/rename")
def rename_file(filename: str, req: RenameFileRequest):
    if filename not in files_db:
        raise HTTPException(status_code=404, detail="파일이 존재하지 않습니다.")
    if req.new_filename in files_db:
        raise HTTPException(status_code=400, detail="이미 존재하는 파일명입니다.")
    files_db[req.new_filename] = files_db.pop(filename)
    return {"message": "파일명 변경 완료", "old": filename, "new": req.new_filename}

# 터미널 WebSocket 엔드포인트
@app.websocket("/terminal")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket connection accepted")

    # pty(pseudo-terminal)를 사용하여 쉘 프로세스 생성
    shell = "/bin/zsh"
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen([shell], stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    print(f"Shell process started with PID: {process.pid}")

    loop = asyncio.get_event_loop()

    def handle_pty_output():
        try:
            data = os.read(master_fd, 1024)
            if data:
                # 비동기적으로 WebSocket으로 데이터 전송
                loop.call_soon(asyncio.create_task, websocket.send_text(data.decode()))
            else:
                # EOF (쉘 종료) 감지
                print("EOF from pty, closing WebSocket")
                loop.call_soon(asyncio.create_task, websocket.close())
        except OSError as e:
            print(f"Error reading from pty: {e}")
            # 오류 발생 시 WebSocket 연결 종료
            loop.call_soon(asyncio.create_task, websocket.close())
        except Exception as e:
             print(f"Unexpected error in handle_pty_output: {e}")
             loop.call_soon(asyncio.create_task, websocket.close())


    # pty의 master_fd에서 읽을 데이터가 있을 때 handle_pty_output 호출
    loop.add_reader(master_fd, handle_pty_output)
    print("pty reader added to event loop.")

    try:
        # WebSocket으로부터 데이터 수신 및 쉘 입력으로 전달
        while True:
            data = await websocket.receive_text()
            # Replace carriage return with carriage return + newline for shell compatibility
            # xterm.js usually sends \r for Enter, but shells expect \r\n or \n
            processed_data = data.replace('\r', '\r\n').encode()
            os.write(master_fd, processed_data)
    except Exception as e:
        print(f"WebSocket receive error or connection closed: {e}")
    finally:
        print("WebSocket receive loop finished.")
        # 정리 작업
        loop.remove_reader(master_fd)
        print("pty reader removed from event loop.")
        process.terminate()
        print(f"Shell process {process.pid} terminated.")
        try:
            await websocket.close()
            print("WebSocket connection closed.")
        except Exception as e:
            print(f"Error closing WebSocket: {e}") 