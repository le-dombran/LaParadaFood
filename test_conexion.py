import httpx

try:
    r = httpx.get("https://ghxbjynsgdxldyqcrnfu.supabase.co", timeout=5.0)
    print("¡Conexión exitosa! Código:", r.status_code)
except Exception as e:
    print("Error exacto:", type(e), e)