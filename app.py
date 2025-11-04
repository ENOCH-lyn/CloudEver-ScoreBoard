from ceboard.main import app

if __name__ == "__main__":
    # 便于本地开发直接运行：python app.py
    import uvicorn
    uvicorn.run("ceboard.main:app", host="127.0.0.1", port=52123, reload=True)
