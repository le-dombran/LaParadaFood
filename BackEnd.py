from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import traceback
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ghxbjynsgdxldyqcrnfu.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdoeGJqeW5zZ2R4bGR5cWNybmZ1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg2NTkwOTQsImV4cCI6MjEwNDIzNTA5NH0.LX3qPzAzuVaQKDUQX9BbUBqz5V9OW6jca-LG3K7O7ko")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="La Parada food Manager Pro")

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

class IngredienteUpdate(BaseModel):
    nombre: Optional[str] = None
    unidad_medida: Optional[str] = None
    costo_unitario: Optional[float] = None
    stock_actual: Optional[float] = None

class RecetaItem(BaseModel):
    ingredient_id: int
    cantidad_usada: float

class ProductoCreate(BaseModel):
    nombre: str
    precio: float
    categoria: str
    receta: Optional[List[RecetaItem]] = []

class VentaItem(BaseModel):
    product_id: int
    cantidad: int
    extras: Optional[List[RecetaItem]] = [] 
    excluidos: Optional[List[int]] = []     

class VentaCreate(BaseModel):
    items: List[VentaItem]
    metodo_pago: str
    cliente_nombre: str
    cliente_telefono: Optional[str] = ""
    cliente_direccion: Optional[str] = ""
    registrado_por: str

class GastoCreate(BaseModel):
    descripcion: str
    monto: float


# --- FUNCIÓN AUXILIAR PARA OBTENER EL INICIO DEL TURNO DIARIO ---
def obtener_inicio_diario():
    try:
        closure_res = supabase.table("cash_closures").select("closed_at").order("closed_at", desc=True).limit(1).execute()
        if closure_res.data and len(closure_res.data) > 0:
            last_closure_str = closure_res.data[0].get("closed_at")
            if last_closure_str:
                return datetime.fromisoformat(last_closure_str.replace("Z", "+00:00"))
    except Exception as e:
        print("ERROR OBTENIENDO CIERRE:", e)
    
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


# --- RUTAS DE INGREDIENTES / INSUMOS ---

@app.get("/ingredients")
def listar_ingredientes():
    try:
        response = supabase.table("ingredients").select("*").execute()
        return response.data
    except Exception as e:
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
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/ingredients/{id}")
def actualizar_insumo(id: int, data: IngredienteUpdate):
    try:
        update_data = {k: v for k, v in data.dict().items() if v is not None}
        if not update_data:
            raise HTTPException(status_code=400, detail="Sin datos para actualizar")
        
        response = supabase.table("ingredients").update(update_data).eq("id", id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Insumo no encontrado")
        return {"mensaje": "Insumo actualizado con éxito", "data": response.data}
    except Exception as e:
        print("ERROR EN PATCH /ingredients:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/ingredients/{id}")
def eliminar_insumo(id: int):
    try:
        # Quitamos el insumo de cualquier receta para evitar errores de relación en la base de datos
        supabase.table("product_ingredients").delete().eq("ingredient_id", id).execute()
        
        # Borramos el insumo
        response = supabase.table("ingredients").delete().eq("id", id).execute()
        return {"mensaje": "Insumo eliminado correctamente"}
    except Exception as e:
        print("ERROR EN DELETE /ingredients:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# --- RUTAS DE PRODUCTOS Y RECETAS ---

@app.get("/products")
def listar_productos():
    try:
        res_prod = supabase.table("products").select("*, product_ingredients(cantidad_usada, ingredient_id, ingredients(*))").execute()
        productos = res_prod.data if res_prod.data else []
        start_diario = obtener_inicio_diario()

        sales_diario_ids = []
        try:
            sales_diario_res = supabase.table("sales").select("id").gte("created_at", start_diario.isoformat()).execute()
            sales_diario_ids = [s["id"] for s in (sales_diario_res.data or [])]
        except Exception:
            pass

        resultado = []
        for p in productos:
            costo_insumos = 0
            receta_formateada = []
            
            for pi in p.get("product_ingredients", []):
                ing = pi.get("ingredients")
                if isinstance(ing, list):
                    ing = ing[0] if len(ing) > 0 else {}
                
                cant = pi.get("cantidad_usada", 0)
                if ing and isinstance(ing, dict):
                    costo_insumos += float(cant or 0) * float(ing.get("costo_unitario", 0) or 0)
                
                receta_formateada.append({
                    "cantidad_usada": cant,
                    "ingredient_id": pi.get("ingredient_id"),
                    "ingredients": ing
                })
            
            precio = p.get("precio", 0)
            ganancia_neta = precio - costo_insumos
            margen = (ganancia_neta / precio * 100) if precio > 0 else 0

            total_vendidos = 0
            try:
                if sales_diario_ids:
                    res_ventas = supabase.table("sale_items").select("cantidad").eq("product_id", p["id"]).in_("sale_id", sales_diario_ids).execute()
                    if res_ventas.data:
                        total_vendidos = sum([item["cantidad"] for item in res_ventas.data])
            except Exception:
                pass

            resultado.append({
                "id": p["id"],
                "nombre": p["nombre"],
                "precio": precio,
                "categoria": p.get("categoria", ""),
                "costo_insumos": round(costo_insumos, 2),
                "ganancia_bruta": round(ganancia_neta, 2),
                "margen_bruto_pct": round(margen, 1),
                "vendidos": total_vendidos,
                "receta": receta_formateada
            })
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/products")
def crear_producto(prod: ProductoCreate):
    try:
        res_prod = supabase.table("products").insert({
            "nombre": prod.nombre, "precio": prod.precio, "categoria": prod.categoria
        }).execute()
        
        new_product_id = res_prod.data[0]["id"]

        if prod.receta:
            for item in prod.receta:
                supabase.table("product_ingredients").insert({
                    "product_id": new_product_id,
                    "ingredient_id": item.ingredient_id,
                    "cantidad_usada": item.cantidad_usada
                }).execute()

        return {"mensaje": "Producto creado con éxito"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/products/{id}")
def editar_producto(id: int, prod: ProductoCreate):
    try:
        supabase.table("products").update({
            "nombre": prod.nombre, "precio": prod.precio, "categoria": prod.categoria
        }).eq("id", id).execute()
        
        supabase.table("product_ingredients").delete().eq("product_id", id).execute()
        
        if prod.receta:
            for item in prod.receta:
                supabase.table("product_ingredients").insert({
                    "product_id": id,
                    "ingredient_id": item.ingredient_id,
                    "cantidad_usada": item.cantidad_usada
                }).execute()

        return {"mensaje": "Producto y receta actualizados con éxito"}
    except Exception as e:
        print("ERROR EN PUT /products:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/products/{id}")
def eliminar_producto(id: int):
    try:
        supabase.table("sale_items").delete().eq("product_id", id).execute()
        supabase.table("product_ingredients").delete().eq("product_id", id).execute()
        response = supabase.table("products").delete().eq("id", id).execute()
        return {"mensaje": "Producto eliminado correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- RUTAS DE VENTAS, GASTOS Y CIERRE DE CAJA ---

@app.get("/sales")
def listar_ventas():
    try:
        response = supabase.table("sales").select("*").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ventas")
def registrar_venta(venta: VentaCreate):
    try:
        total_venta = 0
        detalles_productos = []
        insumos_a_descontar = {} 

        for item in venta.items:
            prod_res = supabase.table("products").select("*, product_ingredients(cantidad_usada, ingredient_id, ingredients(nombre, costo_unitario, stock_actual))").eq("id", item.product_id).execute()
            if not prod_res.data:
                raise HTTPException(status_code=404, detail=f"Producto ID {item.product_id} no encontrado")
            
            prod = prod_res.data[0]
            precio_item_final = float(prod["precio"])
            
            for pi in prod.get("product_ingredients", []):
                ing_id = pi.get("ingredient_id")
                if ing_id in item.excluidos:
                    continue 
                
                cant_requerida = float(pi.get("cantidad_usada", 0) or 0) * item.cantidad
                ing_info = pi.get("ingredients")
                if isinstance(ing_info, list):
                    ing_info = ing_info[0] if len(ing_info) > 0 else {}
                
                if ing_info and isinstance(ing_info, dict):
                    insumos_a_descontar[ing_id] = insumos_a_descontar.get(ing_id, 0) + cant_requerida
                    if float(ing_info.get("stock_actual", 0) or 0) < insumos_a_descontar[ing_id]:
                        raise HTTPException(status_code=400, detail=f"Stock insuficiente de '{ing_info.get('nombre')}'")

            for extra in item.extras:
                ext_res = supabase.table("ingredients").select("*").eq("id", extra.ingredient_id).execute()
                if ext_res.data:
                    ext_info = ext_res.data[0]
                    cant_requerida = float(extra.cantidad_usada) * item.cantidad
                    insumos_a_descontar[extra.ingredient_id] = insumos_a_descontar.get(extra.ingredient_id, 0) + cant_requerida
                    
                    if float(ext_info.get("stock_actual", 0) or 0) < insumos_a_descontar[extra.ingredient_id]:
                        raise HTTPException(status_code=400, detail=f"Stock insuficiente del extra '{ext_info.get('nombre')}'")
                    
                    costo_extra_total = float(ext_info.get("costo_unitario", 0)) * float(extra.cantidad_usada)
                    precio_item_final += (costo_extra_total * 2)

            total_venta += precio_item_final * item.cantidad
            detalles_productos.append((prod, item.cantidad))

        sale_res = supabase.table("sales").insert({
            "total": total_venta,
            "metodo_pago": venta.metodo_pago,
            "cliente_nombre": venta.cliente_nombre,
            "cliente_telefono": venta.cliente_telefono,
            "cliente_direccion": venta.cliente_direccion,
            "registrado_por": venta.registrado_por
        }).execute()
        
        sale_id = sale_res.data[0]["id"]

        for prod, cantidad_vendida in detalles_productos:
            supabase.table("sale_items").insert({
                "sale_id": sale_id, "product_id": prod["id"], "cantidad": cantidad_vendida
            }).execute()

        for ing_id, cant_total in insumos_a_descontar.items():
            ing_res = supabase.table("ingredients").select("stock_actual").eq("id", ing_id).execute()
            if ing_res.data:
                nuevo_stock = float(ing_res.data[0].get("stock_actual", 0)) - cant_total
                supabase.table("ingredients").update({
                    "stock_actual": round(max(0.0, nuevo_stock), 4)
                }).eq("id", ing_id).execute()

        return {"mensaje": "Venta cobrada con éxito", "total_cobrado": total_venta}
    except HTTPException as he:
        raise he
    except Exception as e:
        print("ERROR EN POST /ventas:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/gastos")
def listar_gastos():
    try:
        return supabase.table("expenses").select("*").execute().data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/gastos")
def registrar_gasto(gasto: GastoCreate):
    try:
        response = supabase.table("expenses").insert({"descripcion": gasto.descripcion, "monto": gasto.monto}).execute()
        return {"mensaje": "Gasto registrado", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cierre-caja")
def hacer_cierre_caja():
    try:
        now_str = datetime.now(timezone.utc).isoformat()
        supabase.table("cash_closures").insert({"closed_at": now_str}).execute()
        return {"mensaje": "Cierre de caja realizado con éxito", "closed_at": now_str}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/financial-report")
def reporte_financiero():
    try:
        sales_res = supabase.table("sales").select("*").execute()
        expenses_res = supabase.table("expenses").select("monto, created_at").execute()

        all_sales = sales_res.data or []
        all_expenses = expenses_res.data or []

        productos_res = supabase.table("products").select("id, product_ingredients(cantidad_usada, ingredients(costo_unitario))").execute()
        prod_costo_map = {}
        if productos_res.data: 
            for p in productos_res.data:
                costo_unit_prod = 0
                for pi in p.get("product_ingredients", []):
                    ing = pi.get("ingredients")
                    if isinstance(ing, list): ing = ing[0] if len(ing) > 0 else {}
                    cant = pi.get("cantidad_usada", 0)
                    if ing and isinstance(ing, dict):
                        costo_unit_prod += float(cant or 0) * float(ing.get("costo_unitario", 0) or 0)
                prod_costo_map[p["id"]] = costo_unit_prod

        sale_items_res = supabase.table("sale_items").select("sale_id, product_id, cantidad").execute()
        sale_items_map = {}
        for item in (sale_items_res.data or []):
            s_id = item.get("sale_id")
            if s_id not in sale_items_map:
                sale_items_map[s_id] = []
            sale_items_map[s_id].append(item)

        now = datetime.now(timezone.utc)
        start_diario = obtener_inicio_diario()
        start_month = now - timedelta(days=30)
        start_quarter = now - timedelta(days=90)

        def calcular_metricas(sales_filtradas, expenses_filtradas):
            total_ingresos = sum([float(s.get("total", 0) or 0) for s in sales_filtradas])
            total_gastos_operativos = sum([float(e.get("monto", 0) or 0) for e in expenses_filtradas])
            
            costo_total_insumos_vendidos = 0
            for s in sales_filtradas:
                s_id = s.get("id")
                items = sale_items_map.get(s_id, [])
                for item in items:
                    p_id = item.get("product_id")
                    cant_vendida = item.get("cantidad", 0)
                    costo_unit = prod_costo_map.get(p_id, 0)
                    costo_total_insumos_vendidos += costo_unit * cant_vendida

            egresos_totales = total_gastos_operativos + costo_total_insumos_vendidos
            ganancia_real = total_ingresos - egresos_totales
            porcentaje = (ganancia_real / total_ingresos * 100) if total_ingresos > 0 else 0

            return {
                "ingresos": total_ingresos,
                "gastos": egresos_totales,
                "ganancia": ganancia_real,
                "porcentaje_neto": round(porcentaje, 1)
            }

        sales_diario, expenses_diario = [], []
        sales_month, expenses_month = [], []
        sales_quarter, expenses_quarter = [], []

        for s in all_sales:
            created_str = s.get("created_at")
            if created_str:
                try:
                    dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if dt >= start_quarter: sales_quarter.append(s)
                    if dt >= start_month: sales_month.append(s)
                    if dt >= start_diario: sales_diario.append(s)
                except Exception:
                    pass

        for e in all_expenses:
            created_str = e.get("created_at")
            if created_str:
                try:
                    dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if dt >= start_quarter: expenses_quarter.append(e)
                    if dt >= start_month: expenses_month.append(e)
                    if dt >= start_diario: expenses_diario.append(e)
                except Exception:
                    pass

        return {
            "diario": calcular_metricas(sales_diario, expenses_diario),
            "mensual": calcular_metricas(sales_month, expenses_month),
            "trimestral": calcular_metricas(sales_quarter, expenses_quarter)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    