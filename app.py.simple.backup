from fastapi import FastAPI
import os

app = FastAPI()

@app.get("/")
def root():
    return {"message": "SINCOR is running!", "status": "ok"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

# Uvicorn entry if run locally
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
