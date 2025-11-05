FROM mirror.gcr.io/library/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY . .

# Default data and image directories (also configured via docker-compose volumes)
ENV DATA_DIR=/app/data \
    IMAGE_DIR=/app/images \
    SESSION_SECRET=change-me

EXPOSE 8000
CMD ["uvicorn", "ceboard.main:app", "--host", "0.0.0.0", "--port", "8000"]
