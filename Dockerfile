FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CONTROL_DB=/data/control.db
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home orbit && mkdir /data && chown orbit:orbit /data
COPY backend/ ./backend/
COPY --from=frontend /src/dist/ ./frontend/dist/
USER 10001:10001
EXPOSE 8000
CMD ["python","-m","uvicorn","backend.app:app","--host","0.0.0.0","--port","8000","--no-proxy-headers"]
