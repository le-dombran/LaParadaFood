from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import traceback
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Configuración de Supabase (con respaldo por si dotenv no carga localmente)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ghxbjynsgdxldyqcrnfu.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdoeGJqeW5zZ2R4bGR5cWNybmZ1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg2NTkwOTQsImV4cCI6MjEwNDIzNTA5NH0.LX3qPzAzuVaQKDUQX9BbUBqz5V9OW6jca-LG3K7O7ko")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="La Parada food Manager Pro")

# --- ARCHIVOS ESTÁTICOS Y RUTA PRINCIPAL ---
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def read_root():
    if os.path.exists("static/index.html"):
        with open("static/index.html", "r", encoding="utf-8") as f:
            return f.read()
    return {"message": "La parada food Manager Pro funcionando correctamente"}


# --- MODELOS PYDANTIC ---

class IngredienteCreate(BaseModel):
    nombre: str
    unidad_medida: str
    costo_unitario: float
    stock_actual: Optional[float] = 0.0

class StockUpdate(BaseModel):
    stock_actual: float

class RecetaItem(BaseModel):
    ingredient_id: int
    cantidad_usada: float

class ProductoCreate(BaseModel):
    nombre: str
    precio: float
    stock: int
    categoria: str
    receta: Optional[List[RecetaItem]] = []

class VentaItem(BaseModel):
    product_id: int
    cantidad: int

class VentaCreate(BaseModel):
    items: List[VentaItem]
    metodo_pago: str
    cliente_nombre: str      # A quién se le vendió
    registrado_por: str     # Empleado / Cajero que ingresó el pedido

class GastoCreate(BaseModel):
    descripcion: str
    monto: float


# --- RUTAS DE INGREDIENTES / INSUMOS ---

@app.get("/ingredients")
def listar_ingredientes():
    try:
        response = supabase.table("ingredients").select("*").execute()
        return response.data
    except Exception as e:
        print("ERROR EN /ingredients:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ingredients")
def crear_ingrediente(ing: IngredienteCreate):
    try:
        data = {
            "nombre": ing.nombre,
            "unidad_medida": ing.unidad_medida,
            "costo_unitario": ing.costo_unitario,
            "stock_actual": ing.stock_actual
        }
        response = supabase.table("ingredients").insert(data).execute()
        return {"mensaje": "Insumo creado con éxito", "data": response.data}
    except Exception as e:
        print("ERROR EN POST /ingredients:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/ingredients/{id}/stock")
def actualizar_stock_insumo(id: int, data: StockUpdate):
    try:
        response = supabase.table("ingredients").update({"stock_actual": data.stock_actual}).eq("id", id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Insumo no encontrado")
        return {"mensaje": "Stock actualizado con éxito", "data": response.data}
    except Exception as e:
        print("ERROR EN PATCH /ingredients/stock:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# --- RUTAS DE PRODUCTOS Y RECETAS ---

@app.get("/products")
def listar_productos():
    try:
        res_prod = supabase.table("products").select("*, product_ingredients(cantidad_usada, ingredient_id, ingredients(*))").execute()
        productos = res_prod.data if res_prod.data else []
        
        resultado = []
        for p in productos:
            costo_insumos = 0
            receta_formateada = []
            
            pi_list = p.get("product_ingredients", [])
            for pi in pi_list:
                ing = pi.get("ingredients")
                if isinstance(ing, list):
                    ing = ing[0] if len(ing) > 0 else {}
                
                cant = pi.get("cantidad_usada", 0)
                if ing and isinstance(ing, dict):
                    costo_insumos += float(cant or 0) * float(ing.get("costo_unitario", 0) or 0)
                
                receta_formateada.append({
                    "cantidad_usada": cant,
                    "ingredients": ing
                })
            
            precio = p.get("precio", 0)
            ganancia_neta = precio - costo_insumos
            margen = (ganancia_neta / precio * 100) if precio > 0 else 0

            total_vendidos = 0
            try:
                res_ventas = supabase.table("sale_items").select("cantidad").eq("product_id", p["id"]).execute()
                if res_ventas.data:
                    total_vendidos = sum([item["cantidad"] for item in res_ventas.data])
            except Exception:
                pass

            resultado.append({
                "id": p["id"],
                "nombre": p["nombre"],
                "precio": precio,
                "stock": p.get("stock", 0),
                "categoria": p.get("categoria", ""),
                "costo_insumos": round(costo_insumos, 2),
                "ganancia_bruta": round(ganancia_neta, 2),
                "margen_bruto_pct": round(margen, 1),
                "vendidos": total_vendidos,
                "receta": receta_formateada
            })
        return resultado
    except Exception as e:
        print("ERROR EN /products:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/products")
def crear_producto(prod: ProductoCreate):
    try:
        prod_data = {
            "nombre": prod.nombre,
            "precio": prod.precio,
            "stock": prod.stock,
            "categoria": prod.categoria
        }
        res_prod = supabase.table("products").insert(prod_data).execute()
        if not res_prod.data:
            raise HTTPException(status_code=400, detail="No se pudo crear el producto")
        
        new_product_id = res_prod.data[0]["id"]

        if prod.receta:
            for item in prod.receta:
                rel_data = {
                    "product_id": new_product_id,
                    "ingredient_id": item.ingredient_id,
                    "cantidad_usada": item.cantidad_usada
                }
                try:
                    supabase.table("product_ingredients").insert(rel_data).execute()
                except Exception:
                    pass

        return {"mensaje": "Producto creado con éxito"}
    except Exception as e:
        print("ERROR EN POST /products:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/products/{id}")
def eliminar_producto(id: int):
    try:
        try:
            supabase.table("sale_items").delete().eq("product_id", id).execute()
        except Exception:
            pass

        try:
            supabase.table("product_ingredients").delete().eq("product_id", id).execute()
        except Exception:
            pass

        response = supabase.table("products").delete().eq("id", id).execute()
        return {"mensaje": "Producto eliminado correctamente", "data": response.data}
    except Exception as e:
        print("ERROR EN DELETE /products:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# --- RUTAS DE VENTAS Y GASTOS ---

@app.get("/sales")
def listar_ventas():
    try:
        response = supabase.table("sales").select("*").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        print("ERROR EN /sales:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ventas")
def registrar_venta(venta: VentaCreate):
    try:
        total_venta = 0
        detalles_productos = []

        for item in venta.items:
            prod_res = supabase.table("products").select("*, product_ingredients(cantidad_usada, ingredient_id, ingredients(costo_unitario, stock_actual))").eq("id", item.product_id).execute()
            if not prod_res.data:
                raise HTTPException(status_code=404, detail=f"Producto ID {item.product_id} no encontrado")
            
            prod = prod_res.data[0]
            if prod["stock"] < item.cantidad:
                raise HTTPException(status_code=400, detail=f"Stock insuficiente para el producto: {prod['nombre']}")
            
            total_venta += prod["precio"] * item.cantidad
            detalles_productos.append((prod, item.cantidad))

        sale_res = supabase.table("sales").insert({
            "total": total_venta,
            "metodo_pago": venta.metodo_pago,
            "cliente_nombre": venta.cliente_nombre,
            "registrado_por": venta.registrado_por
        }).execute()
        
        if not sale_res.data:
            raise HTTPException(status_code=400, detail="Error al registrar la venta general")
            
        sale_id = sale_res.data[0]["id"]

        for prod, cantidad_vendida in detalles_productos:
            try:
                supabase.table("sale_items").insert({
                    "sale_id": sale_id,
                    "product_id": prod["id"],
                    "cantidad": cantidad_vendida
                }).execute()
            except Exception:
                pass

            nuevo_stock_prod = prod["stock"] - cantidad_vendida
            supabase.table("products").update({"stock": nuevo_stock_prod}).eq("id", prod["id"]).execute()

            for pi in prod.get("product_ingredients", []):
                ing_id = pi.get("ingredient_id")
                cant_usada = pi.get("cantidad_usada", 0)
                ing_info = pi.get("ingredients")
                if isinstance(ing_info, list):
                    ing_info = ing_info[0] if len(ing_info) > 0 else {}
                
                if ing_id and ing_info and isinstance(ing_info, dict):
                    stock_actual_insumo = float(ing_info.get("stock_actual", 0) or 0)
                    total_a_descontar = float(cant_usada or 0) * cantidad_vendida
                    nuevo_stock_insumo = stock_actual_insumo - total_a_descontar

                    supabase.table("ingredients").update({
                        "stock_actual": round(max(0.0, nuevo_stock_insumo), 4)
                    }).eq("id", ing_id).execute()

        return {"mensaje": "Venta cobrada con éxito", "total_cobrado": total_venta}
    except Exception as e:
        print("ERROR EN POST /ventas:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/gastos")
def listar_gastos():
    try:
        response = supabase.table("expenses").select("*").execute()
        return response.data
    except Exception as e:
        print("ERROR EN /gastos:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/gastos")
def registrar_gasto(gasto: GastoCreate):
    try:
        response = supabase.table("expenses").insert({
            "descripcion": gasto.descripcion,
            "monto": gasto.monto
        }).execute()
        return {"mensaje": "Gasto registrado", "data": response.data}
    except Exception as e:
        print("ERROR EN POST /gastos:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/financial-report")
def reporte_financiero():
    try:
        sales_res = supabase.table("sales").select("*").execute()
        expenses_res = supabase.table("expenses").select("monto").execute()

        total_ingresos = sum([float(s.get("total", 0) or 0) for s in (sales_res.data or [])])
        total_gastos_operativos = sum([float(e.get("monto", 0) or 0) for e in (expenses_res.data or [])])
        
        costo_total_insumos_vendidos = 0
        sale_items_res = supabase.table("sale_items").select("product_id, cantidad").execute()
        
        if sale_items_res.data:
            productos_res = supabase.table("products").select("id, product_ingredients(cantidad_usada, ingredients(costo_unitario))").execute()
            prod_costo_map = {}
            if productos_res.data:
                for p in productos_res.data:
                    costo_unit_prod = 0
                    for pi in p.get("product_ingredients", []):
                        ing = pi.get("ingredients")
                        if isinstance(ing, list):
                            ing = ing[0] if len(ing) > 0 else {}
                        cant = pi.get("cantidad_usada", 0)
                        if ing and isinstance(ing, dict):
                            costo_unit_prod += float(cant or 0) * float(ing.get("costo_unitario", 0) or 0)
                    prod_costo_map[p["id"]] = costo_unit_prod
            
            for item in sale_items_res.data:
                p_id = item.get("product_id")
                cant_vendida = item.get("cantidad", 0)
                costo_unit = prod_costo_map.get(p_id, 0)
                costo_total_insumos_vendidos += costo_unit * cant_vendida

        egresos_totales = total_gastos_operativos + costo_total_insumos_vendidos
        ganancia_real = total_ingresos - egresos_totales
        porcentaje = (ganancia_real / total_ingresos * 100) if total_ingresos > 0 else 0

        data_periodo = {
            "ingresos": total_ingresos,
            "gastos": egresos_totales,
            "ganancia": ganancia_real,
            "porcentaje_neto": round(porcentaje, 1)
        }

        return {
            "diario": data_periodo,
            "mensual": data_periodo,
            "trimestral": data_periodo
        }
    except Exception as e:
        print("ERROR EN /financial-report:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))