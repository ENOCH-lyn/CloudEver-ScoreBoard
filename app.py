from ceboard.main import app

#本地测试运行
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ceboard.main:app", host="127.0.0.1", port=54322, reload=True)
