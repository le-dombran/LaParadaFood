import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configuración de Supabase
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# --- 1. AQUÍ PONES EL CÓDIGO DE LOS ARCHIVOS ESTÁTICOS ---
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def read_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()
# ---------------------------------------------------------

# Tus rutas de productos que ya tenías hechas
@app.get("/products")
def get_products():
    response = supabase.table("products").select("*").execute()
    return response.data