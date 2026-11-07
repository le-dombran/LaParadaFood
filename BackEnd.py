from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import traceback
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ghxbjynsgdxldyqcrnfu.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdoeGJqeW5zZ2R4bGR5cWNybmZ1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg2NTkwOTQsImV4cCI6MjEwNDIzNTA5NH0.LX3qPzAzuVaQKDUQX9BbUBqz5V9OW6jca-LG3K7O7ko")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="La Parada Food Manager Pro")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def read_root():
    if os.path.exists("static/index.html"):
        with open("static/index.html", "r", encoding="utf-8") as f:
            return f.read()
    return {"message": "La Parada Food Manager Pro funcionando correctamente"}


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

class CancelarItemRequest(BaseModel):
    item_index: int
    cantidad: Optional[int] = 1

class GastoCreate(BaseModel):
    descripcion: str
    monto: float


# --- FUNCIONES AUXILIARES ---

def limpiar_numero(valor: float):
    """Convierte flotantes a enteros si no tienen decimales para evitar conflictos de tipos."""
    redondeado = round(float(valor or 0.0), 4)
    return int(redondeado) if redondeado.is_integer() else redondeado

def normalizar_unidad(unidad: str):
    """Filtra y fuerza la unidad únicamente a gramos (gr) o unidades (un)."""
    u = (unidad or "").strip().lower()
    return "gr" if u in ["gr", "g", "gramos", "gramo", "ml"] else "un"

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

def extraer_meta_venta(registro_str: str):
    """Extrae metadatos comprimidos de la comanda."""
    if not registro_str:
        return "", None
    if "||META||" in registro_str:
        partes = registro_str.split("||META||", 1)
        cajero_limpio = partes[0].strip()
        try:
            meta = json.loads(partes[1])
            return cajero_limpio, meta
        except Exception:
            return cajero_limpio, None
    return registro_str, None

def construir_textos_resumen(meta_items: list):
    lineas = []
    for it in meta_items:
        q_act = int(it.get("q", 0) or 0)
        q_canc = int(it.get("q_canc", 0) or 0)
        nombre = it.get("nombre", f"Plato #{it.get('p')}")
        nota = it.get("nota", "")
        if q_act > 0:
            texto = f"{q_act}x {nombre}{nota}"
            if q_canc > 0:
                texto += f" [Cancelado: {q_canc}x]"
            lineas.append(texto)
        elif q_canc > 0:
            lineas.append(f"[CANCELADO] {q_canc}x {nombre}{nota}")
    return lineas

def devolver_insumos_al_stock(insumos_por_unidad: dict, unidades_a_devolver: int):
    """Reintegra al inventario los gramos o unidades de los platos cancelados."""
    for ing_id_str, cant_unitaria in (insumos_por_unidad or {}).items():
        ing_id = int(ing_id_str)
        cant_devolver = float(cant_unitaria) * float(unidades_a_devolver)
        if cant_devolver <= 0:
            continue
        ing_res = supabase.table("ingredients").select("nombre, stock_actual, unidad_medida").eq("id", ing_id).execute()
        if ing_res.data:
            ing_data = ing_res.data[0]
            stock_actual = float(ing_data.get("stock_actual", 0) or 0)
            nuevo_stock = limpiar_numero(stock_actual + cant_devolver)
            upd = supabase.table("ingredients").update({"stock_actual": nuevo_stock}).eq("id", ing_id).execute()
            print(f"📈 REINTEGRO - {ing_data['nombre']}: {stock_actual} -> {nuevo_stock} {ing_data['unidad_medida']}")


# --- RUTAS DE INGREDIENTES / INSUMOS ---

@app.get("/ingredients")
def listar_ingredientes():
    try:
        response = supabase.table("ingredients").select("*").order("id").execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ingredients")
def crear_ingrediente(ing: IngredienteCreate):
    try:
        nombre_limpio = ing.nombre.strip()
        if not nombre_limpio:
            raise HTTPException(status_code=400, detail="El nombre del insumo no puede estar vacío.")

        # Verificar si ya existe para evitar error 500 por clave única
        existente = supabase.table("ingredients").select("id").ilike("nombre", nombre_limpio).execute()
        if existente.data and len(existente.data) > 0:
            raise HTTPException(status_code=400, detail=f"Ya existe un insumo con el nombre '{nombre_limpio}'. Usa la sección de editar para modificarlo.")

        unidad_final = normalizar_unidad(ing.unidad_medida)
        data = {
            "nombre": nombre_limpio,
            "unidad_medida": unidad_final,
            "costo_unitario": limpiar_numero(ing.costo_unitario),
            "stock_actual": limpiar_numero(ing.stock_actual or 0.0)
        }
        response = supabase.table("ingredients").insert(data).execute()
        return {"mensaje": "Insumo creado con éxito", "data": response.data}
    except HTTPException as he:
        raise he
    except Exception as e:
        print("ERROR EN POST /ingredients:", traceback.format_exc())
        raise HTTPException(status_code=400, detail=f"No se pudo guardar el insumo: {str(e)}")

@app.patch("/ingredients/{id}")
def actualizar_insumo(id: int, data: IngredienteUpdate):
    try:
        update_data = {}
        for k, v in data.dict().items():
            if v is not None:
                if k in ("costo_unitario", "stock_actual"):
                    update_data[k] = limpiar_numero(v)
                elif k == "unidad_medida":
                    update_data[k] = normalizar_unidad(v)
                else:
                    update_data[k] = v.strip() if isinstance(v, str) else v
        
        if not update_data:
            raise HTTPException(status_code=400, detail="Sin datos para actualizar.")
        
        response = supabase.table("ingredients").update(update_data).eq("id", id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Insumo no encontrado.")
        return {"mensaje": "Insumo actualizado con éxito", "data": response.data}
    except HTTPException as he:
        raise he
    except Exception as e:
        print("ERROR EN PATCH /ingredients:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/ingredients/{id}")
def eliminar_insumo(id: int):
    try:
        supabase.table("product_ingredients").delete().eq("ingredient_id", id).execute()
        supabase.table("ingredients").delete().eq("id", id).execute()
        return {"mensaje": "Insumo eliminado correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- RUTAS DE PRODUCTOS Y RECETAS ---

@app.get("/products")
def listar_productos():
    try:
        res_prod = supabase.table("products").select("*").order("id").execute()
        productos = res_prod.data or []

        res_pi = supabase.table("product_ingredients").select("*").execute()
        all_pi = res_pi.data or []

        res_ing = supabase.table("ingredients").select("*").execute()
        ing_map = {i["id"]: i for i in (res_ing.data or [])}

        pi_por_producto = {}
        for pi in all_pi:
            p_id = pi.get("product_id")
            if p_id not in pi_por_producto:
                pi_por_producto[p_id] = []
            pi_por_producto[p_id].append(pi)

        start_diario = obtener_inicio_diario()
        sales_diario_ids = []
        try:
            sales_diario_res = supabase.table("sales").select("id").gte("created_at", start_diario.isoformat()).execute()
            sales_diario_ids = [s["id"] for s in (sales_diario_res.data or [])]
        except Exception:
            pass

        resultado = []
        for p in productos:
            costo_insumos = 0.0
            receta_formateada = []
            
            for pi in pi_por_producto.get(p["id"], []):
                ing_id = pi.get("ingredient_id")
                ing = ing_map.get(ing_id, {})
                cant = float(pi.get("cantidad_usada", 0) or 0)

                if ing:
                    costo_insumos += cant * float(ing.get("costo_unitario", 0) or 0)
                
                receta_formateada.append({
                    "cantidad_usada": limpiar_numero(cant),
                    "ingredient_id": ing_id,
                    "ingredients": ing
                })
            
            precio = float(p.get("precio", 0) or 0)
            ganancia_neta = precio - costo_insumos
            margen = (ganancia_neta / precio * 100) if precio > 0 else 0

            total_vendidos = 0
            try:
                if sales_diario_ids:
                    res_ventas = supabase.table("sale_items").select("cantidad").eq("product_id", p["id"]).in_("sale_id", sales_diario_ids).execute()
                    if res_ventas.data:
                        total_vendidos = sum([int(item["cantidad"]) for item in res_ventas.data])
            except Exception:
                pass

            resultado.append({
                "id": p["id"],
                "nombre": p["nombre"],
                "precio": limpiar_numero(precio),
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
            "nombre": prod.nombre.strip(),
            "precio": limpiar_numero(prod.precio),
            "categoria": prod.categoria.strip()
        }).execute()
        
        new_product_id = res_prod.data[0]["id"]

        if prod.receta:
            for item in prod.receta:
                supabase.table("product_ingredients").insert({
                    "product_id": new_product_id,
                    "ingredient_id": item.ingredient_id,
                    "cantidad_usada": limpiar_numero(item.cantidad_usada)
                }).execute()

        return {"mensaje": "Producto creado con éxito"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/products/{id}")
def editar_producto(id: int, prod: ProductoCreate):
    try:
        supabase.table("products").update({
            "nombre": prod.nombre.strip(),
            "precio": limpiar_numero(prod.precio),
            "categoria": prod.categoria.strip()
        }).eq("id", id).execute()
        
        supabase.table("product_ingredients").delete().eq("product_id", id).execute()
        
        if prod.receta:
            for item in prod.receta:
                supabase.table("product_ingredients").insert({
                    "product_id": id,
                    "ingredient_id": item.ingredient_id,
                    "cantidad_usada": limpiar_numero(item.cantidad_usada)
                }).execute()

        return {"mensaje": "Producto y receta actualizados con éxito"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/products/{id}")
def eliminar_producto(id: int):
    try:
        supabase.table("sale_items").delete().eq("product_id", id).execute()
        supabase.table("product_ingredients").delete().eq("product_id", id).execute()
        supabase.table("products").delete().eq("id", id).execute()
        return {"mensaje": "Producto eliminado correctamente"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- RUTAS DE VENTAS, CANCELACIONES, GASTOS Y CIERRE DE CAJA ---

@app.get("/sales")
def listar_ventas():
    try:
        response = supabase.table("sales").select("*").order("created_at", desc=True).execute()
        ventas = response.data or []

        prod_res = supabase.table("products").select("id, nombre, precio").execute()
        prod_map = {p["id"]: p for p in (prod_res.data or [])}

        items_res = supabase.table("sale_items").select("sale_id, product_id, cantidad").execute()
        items_por_venta = {}
        for it in (items_res.data or []):
            s_id = it.get("sale_id")
            if s_id not in items_por_venta:
                items_por_venta[s_id] = []
            p_info = prod_map.get(it.get("product_id"), {})
            nombre_p = p_info.get("nombre", f"Plato #{it.get('product_id')}")
            items_por_venta[s_id].append({
                "p": it.get("product_id"),
                "nombre": nombre_p,
                "q": it.get("cantidad", 1),
                "q_canc": 0,
                "precio_u": float(p_info.get("precio", 0) or 0),
                "nota": ""
            })

        start_diario = obtener_inicio_diario()
        resultado = []

        for v in ventas:
            cajero_limpio, meta = extraer_meta_venta(v.get("registrado_por", ""))
            anulada = False
            total_orig = float(v.get("total", 0) or 0)
            items_estructurados = []
            detalles_pedido = []

            if meta and isinstance(meta, dict):
                anulada = bool(meta.get("anulada", False))
                total_orig = float(meta.get("total_orig", v.get("total", 0)) or 0)
                if "i" in meta and isinstance(meta["i"], list):
                    for idx, it in enumerate(meta["i"]):
                        p_id = it.get("p")
                        p_info = prod_map.get(p_id, {})
                        nombre_plato = it.get("nombre") or p_info.get("nombre", f"Plato #{p_id}")
                        items_estructurados.append({
                            "index": idx,
                            "p": p_id,
                            "nombre": nombre_plato,
                            "q": int(it.get("q", 0) or 0),
                            "q_canc": int(it.get("q_canc", 0) or 0),
                            "precio_u": float(it.get("precio_u", p_info.get("precio", 0)) or 0),
                            "nota": it.get("nota", "")
                        })
                    detalles_pedido = construir_textos_resumen(items_estructurados)
                elif "d" in meta:
                    detalles_pedido = meta["d"]
            else:
                fallback_items = items_por_venta.get(v["id"], [])
                for idx, it in enumerate(fallback_items):
                    it["index"] = idx
                    items_estructurados.append(it)
                detalles_pedido = [f"{it['q']}x {it['nombre']}" for it in fallback_items] or ["Sin detalle de platos"]

            es_turno_actual = False
            created_str = v.get("created_at")
            if created_str:
                try:
                    dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    es_turno_actual = (dt >= start_diario)
                except Exception:
                    pass

            v_copia = dict(v)
            v_copia["registrado_por"] = cajero_limpio
            v_copia["anulada"] = anulada
            v_copia["total_orig"] = round(total_orig, 2)
            v_copia["items_detalle"] = items_estructurados
            v_copia["detalles_pedido"] = detalles_pedido
            v_copia["turno_actual"] = es_turno_actual
            resultado.append(v_copia)

        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ventas")
def registrar_venta(venta: VentaCreate):
    try:
        total_venta = 0.0
        detalles_productos = []
        insumos_a_descontar = {}
        textos_resumen_pedido = []
        meta_items_costeo = []

        all_ing_res = supabase.table("ingredients").select("*").execute()
        ing_map = {i["id"]: i for i in (all_ing_res.data or [])}

        for item in venta.items:
            prod_res = supabase.table("products").select("*").eq("id", item.product_id).execute()
            if not prod_res.data:
                raise HTTPException(status_code=404, detail=f"Producto ID {item.product_id} no encontrado")
            
            prod = prod_res.data[0]
            precio_item_final = float(prod["precio"])

            pi_res = supabase.table("product_ingredients").select("ingredient_id, cantidad_usada").eq("product_id", item.product_id).execute()
            receta_plato = pi_res.data or []

            excluidos_ids = item.excluidos or []
            extras_list = item.extras or []

            insumos_activos_usados = 0
            nombres_excluidos = []
            nombres_extras = []
            insumos_por_unidad = {}

            # Receta base
            for pi in receta_plato:
                ing_id = pi.get("ingredient_id")
                ing_info = ing_map.get(ing_id)
                if not ing_info:
                    continue

                if ing_id in excluidos_ids:
                    nombres_excluidos.append(ing_info.get("nombre", f"ID {ing_id}"))
                    continue
                
                insumos_activos_usados += 1
                cant_unit = float(pi.get("cantidad_usada", 0) or 0)
                cant_requerida = cant_unit * item.cantidad
                insumos_por_unidad[str(ing_id)] = round(insumos_por_unidad.get(str(ing_id), 0.0) + cant_unit, 4)
                insumos_a_descontar[ing_id] = insumos_a_descontar.get(ing_id, 0.0) + cant_requerida
                
                stock_disponible = float(ing_info.get("stock_actual", 0) or 0)
                if stock_disponible < insumos_a_descontar[ing_id]:
                    unidad_lbl = ing_info.get("unidad_medida", "un")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Stock insuficiente de '{ing_info.get('nombre')}' (Disponible: {stock_disponible} {unidad_lbl}, Requerido: {insumos_a_descontar[ing_id]:g} {unidad_lbl})"
                    )

            # Extras
            extras_meta = []
            for extra in extras_list:
                ext_info = ing_map.get(extra.ingredient_id)
                if ext_info:
                    insumos_activos_usados += 1
                    cant_unit_ext = float(extra.cantidad_usada)
                    cant_requerida = cant_unit_ext * item.cantidad
                    insumos_por_unidad[str(extra.ingredient_id)] = round(insumos_por_unidad.get(str(extra.ingredient_id), 0.0) + cant_unit_ext, 4)
                    insumos_a_descontar[extra.ingredient_id] = insumos_a_descontar.get(extra.ingredient_id, 0.0) + cant_requerida
                    
                    stock_disponible = float(ext_info.get("stock_actual", 0) or 0)
                    if stock_disponible < insumos_a_descontar[extra.ingredient_id]:
                        unidad_lbl = ext_info.get("unidad_medida", "un")
                        raise HTTPException(
                            status_code=400,
                            detail=f"Stock insuficiente del extra '{ext_info.get('nombre')}' (Disponible: {stock_disponible} {unidad_lbl}, Requerido: {insumos_a_descontar[extra.ingredient_id]:g} {unidad_lbl})"
                        )
                    
                    costo_extra_total = float(ext_info.get("costo_unitario", 0)) * cant_unit_ext
                    precio_item_final += (costo_extra_total * 2)
                    unidad_ext = ext_info.get("unidad_medida", "")
                    nombres_extras.append(f"{cant_unit_ext:g}{unidad_ext} {ext_info.get('nombre')}")
                    extras_meta.append({"id": extra.ingredient_id, "q": limpiar_numero(cant_unit_ext)})

            if insumos_activos_usados == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"El plato '{prod['nombre']}' no tiene ningún insumo activo. Agrega ingredientes a su receta antes de venderlo."
                )

            precio_item_final = round(precio_item_final, 2)
            total_venta += precio_item_final * item.cantidad
            detalles_productos.append((prod["id"], item.cantidad))

            notas = []
            if nombres_excluidos:
                notas.append(f"Sin: {', '.join(nombres_excluidos)}")
            if nombres_extras:
                notas.append(f"Extra: {', '.join(nombres_extras)}")
            nota_str = f" ({' | '.join(notas)})" if notas else ""
            textos_resumen_pedido.append(f"{item.cantidad}x {prod['nombre']}{nota_str}")

            meta_items_costeo.append({
                "p": prod["id"],
                "nombre": prod["nombre"],
                "q": item.cantidad,
                "q_canc": 0,
                "precio_u": limpiar_numero(precio_item_final),
                "ex": excluidos_ids,
                "ext": extras_meta,
                "insumos_u": {k: limpiar_numero(v) for k, v in insumos_por_unidad.items()},
                "nota": nota_str
            })

        # Descontar stock directamente
        for ing_id, cant_total in insumos_a_descontar.items():
            ing_data = ing_map.get(ing_id)
            if ing_data:
                stock_previo = float(ing_data.get("stock_actual", 0) or 0)
                nuevo_stock = limpiar_numero(max(0.0, stock_previo - cant_total))
                supabase.table("ingredients").update({"stock_actual": nuevo_stock}).eq("id", ing_id).execute()

        # Guardar venta
        total_venta = limpiar_numero(total_venta)
        meta_payload = json.dumps({
            "anulada": False,
            "total_orig": total_venta,
            "d": textos_resumen_pedido,
            "i": meta_items_costeo
        }, ensure_ascii=False)
        registrado_por_meta = f"{venta.registrado_por.strip()} ||META||{meta_payload}"

        sale_res = supabase.table("sales").insert({
            "total": total_venta,
            "metodo_pago": venta.metodo_pago,
            "cliente_nombre": venta.cliente_nombre.strip(),
            "cliente_telefono": (venta.cliente_telefono or "").strip(),
            "cliente_direccion": (venta.cliente_direccion or "").strip(),
            "registrado_por": registrado_por_meta
        }).execute()
        
        sale_id = sale_res.data[0]["id"]

        cant_por_prod = {}
        for p_id, cant_v in detalles_productos:
            cant_por_prod[p_id] = cant_por_prod.get(p_id, 0) + cant_v

        for p_id, cant_total_prod in cant_por_prod.items():
            supabase.table("sale_items").insert({
                "sale_id": sale_id, "product_id": p_id, "cantidad": cant_total_prod
            }).execute()

        return {"mensaje": "Venta cobrada con éxito", "total_cobrado": total_venta}
    except HTTPException as he:
        raise he
    except Exception as e:
        print("ERROR EN POST /ventas:", traceback.format_exc())
        raise HTTPException(status_code=400, detail=f"Error registrando venta: {str(e)}")

@app.post("/ventas/{sale_id}/cancelar")
def cancelar_pedido_completo(sale_id: int):
    try:
        sale_res = supabase.table("sales").select("*").eq("id", sale_id).execute()
        if not sale_res.data:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")
        
        venta = sale_res.data[0]
        cajero_limpio, meta = extraer_meta_venta(venta.get("registrado_por", ""))

        if meta and meta.get("anulada"):
            raise HTTPException(status_code=400, detail="Este pedido ya se encuentra anulado.")

        if meta and isinstance(meta, dict) and "i" in meta:
            for it in meta["i"]:
                q_activa = int(it.get("q", 0) or 0)
                if q_activa > 0:
                    insumos_u = it.get("insumos_u", {})
                    if not insumos_u:
                        p_res = supabase.table("product_ingredients").select("ingredient_id, cantidad_usada").eq("product_id", it.get("p")).execute()
                        excluidos = it.get("ex", []) or []
                        for pi in (p_res.data or []):
                            if pi["ingredient_id"] not in excluidos:
                                insumos_u[str(pi["ingredient_id"])] = float(pi.get("cantidad_usada", 0) or 0)
                        for ext in (it.get("ext", []) or []):
                            ext_id_str = str(ext.get("id"))
                            insumos_u[ext_id_str] = insumos_u.get(ext_id_str, 0.0) + float(ext.get("q", 0) or 0)

                    devolver_insumos_al_stock(insumos_u, q_activa)
                    it["q_canc"] = int(it.get("q_canc", 0) or 0) + q_activa
                    it["q"] = 0

            meta["anulada"] = True
            if "total_orig" not in meta:
                meta["total_orig"] = limpiar_numero(venta.get("total", 0) or 0)
            meta["d"] = construir_textos_resumen(meta["i"])
        else:
            items_res = supabase.table("sale_items").select("product_id, cantidad").eq("sale_id", sale_id).execute()
            for s_item in (items_res.data or []):
                p_res = supabase.table("product_ingredients").select("ingredient_id, cantidad_usada").eq("product_id", s_item["product_id"]).execute()
                insumos_u = {str(pi["ingredient_id"]): float(pi["cantidad_usada"]) for pi in (p_res.data or [])}
                devolver_insumos_al_stock(insumos_u, int(s_item.get("cantidad", 1)))
            meta = {
                "anulada": True,
                "total_orig": limpiar_numero(venta.get("total", 0) or 0),
                "d": ["[PEDIDO ANULADO]"],
                "i": []
            }

        nuevo_registro = f"{cajero_limpio} ||META||{json.dumps(meta, ensure_ascii=False)}"
        supabase.table("sales").update({
            "total": 0,
            "registrado_por": nuevo_registro
        }).eq("id", sale_id).execute()

        supabase.table("sale_items").delete().eq("sale_id", sale_id).execute()
        return {"mensaje": f"Pedido #{sale_id} anulado por completo. Insumos reintegrados al stock."}
    except HTTPException as he:
        raise he
    except Exception as e:
        print("ERROR EN CANCELAR PEDIDO:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ventas/{sale_id}/cancelar-item")
def cancelar_item_pedido(sale_id: int, req: CancelarItemRequest):
    try:
        sale_res = supabase.table("sales").select("*").eq("id", sale_id).execute()
        if not sale_res.data:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")
        
        venta = sale_res.data[0]
        cajero_limpio, meta = extraer_meta_venta(venta.get("registrado_por", ""))

        if not meta or "i" not in meta or not isinstance(meta["i"], list):
            raise HTTPException(status_code=400, detail="Este pedido no admite cancelación por plato individual.")

        if meta.get("anulada"):
            raise HTTPException(status_code=400, detail="Este pedido ya está anulado.")

        idx = req.item_index
        if idx < 0 or idx >= len(meta["i"]):
            raise HTTPException(status_code=400, detail="Plato no encontrado en el pedido.")

        item_target = meta["i"][idx]
        q_activa = int(item_target.get("q", 0) or 0)
        if q_activa <= 0:
            raise HTTPException(status_code=400, detail="Este plato ya fue cancelado.")

        cant_a_cancelar = min(max(1, int(req.cantidad or 1)), q_activa)

        insumos_u = item_target.get("insumos_u", {})
        if not insumos_u:
            p_res = supabase.table("product_ingredients").select("ingredient_id, cantidad_usada").eq("product_id", item_target.get("p")).execute()
            excluidos = item_target.get("ex", []) or []
            for pi in (p_res.data or []):
                if pi["ingredient_id"] not in excluidos:
                    insumos_u[str(pi["ingredient_id"])] = float(pi.get("cantidad_usada", 0) or 0)
            for ext in (item_target.get("ext", []) or []):
                ext_id_str = str(ext.get("id"))
                insumos_u[ext_id_str] = insumos_u.get(ext_id_str, 0.0) + float(ext.get("q", 0) or 0)

        devolver_insumos_al_stock(insumos_u, cant_a_cancelar)

        item_target["q"] = q_activa - cant_a_cancelar
        item_target["q_canc"] = int(item_target.get("q_canc", 0) or 0) + cant_a_cancelar

        if "total_orig" not in meta:
            meta["total_orig"] = limpiar_numero(venta.get("total", 0) or 0)

        nuevo_total = 0.0
        total_unidades_activas = 0
        cant_activa_por_producto = {}

        for it in meta["i"]:
            q_rest = int(it.get("q", 0) or 0)
            p_id = it.get("p")
            precio_u = float(it.get("precio_u", 0) or 0)
            if q_rest > 0:
                nuevo_total += q_rest * precio_u
                total_unidades_activas += q_rest
                cant_activa_por_producto[p_id] = cant_activa_por_producto.get(p_id, 0) + q_rest

        if total_unidades_activas == 0:
            meta["anulada"] = True
            nuevo_total = 0.0

        nuevo_total_limpio = limpiar_numero(nuevo_total)
        meta["d"] = construir_textos_resumen(meta["i"])
        nuevo_registro = f"{cajero_limpio} ||META||{json.dumps(meta, ensure_ascii=False)}"

        supabase.table("sales").update({
            "total": nuevo_total_limpio,
            "registrado_por": nuevo_registro
        }).eq("id", sale_id).execute()

        supabase.table("sale_items").delete().eq("sale_id", sale_id).execute()
        for p_id, cant_act in cant_activa_por_producto.items():
            supabase.table("sale_items").insert({
                "sale_id": sale_id, "product_id": p_id, "cantidad": cant_act
            }).execute()

        return {
            "mensaje": f"Se canceló {cant_a_cancelar}x {item_target.get('nombre', 'plato')} y se devolvió su stock.",
            "nuevo_total": nuevo_total_limpio,
            "anulada": meta.get("anulada", False)
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        print("ERROR EN CANCELAR ITEM:", traceback.format_exc())
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
        response = supabase.table("expenses").insert({
            "descripcion": gasto.descripcion.strip(),
            "monto": limpiar_numero(gasto.monto)
        }).execute()
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
        ing_res = supabase.table("ingredients").select("id, costo_unitario").execute()
        pi_res = supabase.table("product_ingredients").select("product_id, ingredient_id, cantidad_usada").execute()

        all_sales = sales_res.data or []
        all_expenses = expenses_res.data or []

        ing_costo_map = {
            i["id"]: float(i.get("costo_unitario", 0) or 0)
            for i in (ing_res.data or [])
        }

        prod_receta_map = {}
        for pi in (pi_res.data or []):
            p_id = pi.get("product_id")
            if p_id not in prod_receta_map:
                prod_receta_map[p_id] = []
            prod_receta_map[p_id].append(pi)

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

        def calcular_costo_venta(s):
            _, meta = extraer_meta_venta(s.get("registrado_por", ""))
            if meta and isinstance(meta, dict) and meta.get("anulada"):
                return 0.0

            costo_venta = 0.0

            if meta and isinstance(meta, dict) and "i" in meta:
                for it in meta["i"]:
                    cant_platos = float(it.get("q", 0) or 0)
                    if cant_platos <= 0:
                        continue
                    p_id = it.get("p")
                    excluidos = it.get("ex", []) or []
                    extras = it.get("ext", []) or []

                    costo_unitario_plato = 0.0
                    for pi in prod_receta_map.get(p_id, []):
                        ing_id = pi.get("ingredient_id")
                        if ing_id in excluidos:
                            continue
                        if ing_id in ing_costo_map:
                            costo_unitario_plato += float(pi.get("cantidad_usada", 0) or 0) * ing_costo_map[ing_id]

                    for ext in extras:
                        ext_id = ext.get("id")
                        if ext_id in ing_costo_map:
                            costo_unitario_plato += float(ext.get("q", 0) or 0) * ing_costo_map[ext_id]

                    costo_venta += costo_unitario_plato * cant_platos
            else:
                s_id = s.get("id")
                for item in sale_items_map.get(s_id, []):
                    p_id = item.get("product_id")
                    cant_vendida = float(item.get("cantidad", 0) or 0)
                    costo_unit = sum([
                        float(pi.get("cantidad_usada", 0) or 0) * ing_costo_map.get(pi.get("ingredient_id"), 0.0)
                        for pi in prod_receta_map.get(p_id, [])
                    ])
                    costo_venta += costo_unit * cant_vendida

            return costo_venta

        def calcular_metricas(sales_filtradas, expenses_filtradas):
            ventas_activas = []
            for s in sales_filtradas:
                _, meta = extraer_meta_venta(s.get("registrado_por", ""))
                if meta and isinstance(meta, dict) and meta.get("anulada"):
                    continue
                ventas_activas.append(s)

            total_ingresos = sum([float(s.get("total", 0) or 0) for s in ventas_activas])
            total_gastos_operativos = sum([float(e.get("monto", 0) or 0) for e in expenses_filtradas])
            costo_total_insumos_vendidos = sum([calcular_costo_venta(s) for s in ventas_activas])

            egresos_totales = total_gastos_operativos + costo_total_insumos_vendidos
            ganancia_real = total_ingresos - egresos_totales
            porcentaje = (ganancia_real / total_ingresos * 100) if total_ingresos > 0 else 0

            return {
                "ingresos": round(total_ingresos, 2),
                "gastos": round(egresos_totales, 2),
                "ganancia": round(ganancia_real, 2),
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
        print("ERROR EN GET /financial-report:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))