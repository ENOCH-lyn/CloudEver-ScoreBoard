from ceboard.main import app

#本地测试运行
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ceboard.main:app", host="0.0.0.0", port=52123, reload=True)
