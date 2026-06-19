FROM python:3.12-slim
WORKDIR /app
COPY . .
EXPOSE 3000
# serve.py は PORT 環境変数を尊重（未指定なら空きポート）
CMD ["python", "serve.py"]
