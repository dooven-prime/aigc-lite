FROM node:22-alpine AS frontend

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY --from=frontend /frontend/dist ./frontend/dist
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["aigc-lite"]
