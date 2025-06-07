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
    return {"output": "실행 결과 예시"}

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
                {"role": "system", "content": "You are a helpful assistant that provides coding suggestions. Respond in the user's language. Your primary goal is to help the user write code. Always provide code suggestions, or ask clarifying questions that directly lead to code generation. When providing code, always wrap your entire response in a JSON object with two top-level keys: 'code_suggestions' (an array of {filename: string, content: string} objects) and 'explanation' (a string). The 'explanation' key should *only* contain natural language explaining the code or asking clarifying questions, and should *not* contain any JSON or code blocks. If you need to ask questions, format them as a numbered list."},
                {"role": "user", "content": req.message}
            ]
        )
        
        ai_response_content = completion.choices[0].message.content
        print(f"Raw AI response content: {ai_response_content}")
        
        json_string = ai_response_content.strip()
        # More robust stripping of markdown code block fences
        if json_string.startswith('```json'):
            json_string = json_string[len('```json'):].strip()
        if json_string.endswith('```'):
            json_string = json_string[:-len('```')].strip()
        
        print(f"Stripped JSON string: {json_string}")
        
        try:
            import json
            parsed_response = json.loads(json_string)
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
        raise HTTPException(status_code=500, detail=str(e))

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