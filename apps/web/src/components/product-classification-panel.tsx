"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Category, CategoryPage, messageFor, OperatorError, post, Product, ReadGeneration, request } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";

export function ProductClassificationPanel({ product, onChanged }: { product: Product; onChanged: (change: { id: string; category_id?: string | null; is_active?: boolean }) => void }) {
  const { capabilities } = useOperator();
  const canList = capabilities.includes("catalog.list_categories");
  const canAssign = capabilities.includes("catalog.set_product_category");
  const canActivate = capabilities.includes("catalog.set_product_active");
  const [categories, setCategories] = useState<Category[]>([]);
  const [after, setAfter] = useState<string | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [categoryError, setCategoryError] = useState("");
  const [selected, setSelected] = useState(product.category_id ?? "");
  const serverCategory = useRef(product.category_id ?? "");
  const [serverChanged, setServerChanged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const reads = useRef(new ReadGeneration());
  const alive = useRef(false);
  const errorRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const incoming = product.category_id ?? "";
    const previous = serverCategory.current;
    if (incoming === previous) return;
    serverCategory.current = incoming;
    if (selected === previous || selected === incoming) {
      setSelected(incoming);
      setServerChanged(false);
    } else setServerChanged(true);
  }, [product.category_id, selected]);
  useEffect(() => {
    if (!canList) return;
    const controller = new AbortController();
    const stream = reads.current;
    const token = stream.begin();
    request<CategoryPage>(`/catalog/categories${after ? `?after=${encodeURIComponent(after)}` : ""}`, { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted || !stream.accepts(token)) return;
      setCategories((previous) => after ? [...previous, ...result.categories.filter((item) => !previous.some((old) => old.id === item.id))] : result.categories);
      setNext(result.next_cursor); setCategoryError(""); setLoading(false);
    }).catch((failure) => { if (!controller.signal.aborted && stream.accepts(token)) { setCategoryError(messageFor(failure)); setLoading(false); } });
    return () => { controller.abort(); stream.invalidate(); };
  }, [after, attempt, canList]);

  async function mutate(kind: "category" | "active", data: { category_id: string | null } | { is_active: boolean }) {
    if (busy || uncertain || (kind === "category" ? !canAssign : !canActivate)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await post<{ id: string; category_id?: string | null; is_active?: boolean }>(`/catalog/products/${encodeURIComponent(product.id)}/${kind}`, data);
      if (!alive.current) return;
      // A malformed successful write response is uncertain, never silently applied.
      if (typeof result !== "object" || result === null || result.id !== product.id || (kind === "active" ? typeof result.is_active !== "boolean" : !(result.category_id === null || typeof result.category_id === "string"))) {
        throw new OperatorError("invalid_response", undefined, true);
      }
      onChanged(result);
      if (kind === "category") { setSelected(result.category_id ?? ""); setServerChanged(false); }
      setNotice(kind === "category" ? "Clasificación guardada." : "Estado administrativo guardado. Esto no habilita ventas.");
    } catch (failure) {
      if (!alive.current) return;
      setError(messageFor(failure));
      if (failure instanceof OperatorError && failure.uncertain) setUncertain(true);
      window.setTimeout(() => errorRef.current?.focus(), 0);
    } finally { if (alive.current) setBusy(false); }
  }
  function assign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void mutate("category", { category_id: selected || null });
  }
  const currentCategory = categories.find((item) => item.id === product.category_id);
  return <section className="panel classification-panel" aria-labelledby="classification-title">
    <div className="section-heading"><div><p className="eyebrow">Organización comercial</p><h2 id="classification-title">Clasificación y estado</h2></div><span className={`badge ${product.is_active ? "badge-active" : "badge-inactive"}`}>{product.is_active ? "Activo administrativamente" : "Inactivo administrativamente"}</span></div>
    <p className="saved-category">Categoría guardada: <strong>{product.category_id ? currentCategory?.name ?? "Categoría asignada" : "Sin categoría"}</strong></p>
    {product.category_id && !currentCategory && <details className="technical-details"><summary>Identificador de categoría</summary><p className="identifier">{product.category_id}</p></details>}
    {error && <p role="alert" tabIndex={-1} ref={errorRef} className="error-box">{error}</p>}
    {notice && <p role="status" className="success-box">{notice}</p>}
    {uncertain && <p className="notice" role="status">Resultado incierto. Usá «Actualizar detalle» para consultar el estado; no reenvíes la escritura. La consulta no confirma quién realizó un cambio.</p>}
    <div className="workspace-grid classification-grid">
      <div>{!canAssign ? <p>Sin permiso para cambiar la categoría.</p> : !canList ? <p>Necesitás permiso de consulta de categorías para elegir una en esta pantalla.</p> : <form onSubmit={assign} aria-busy={busy}>
        <label htmlFor="product-category">Categoría del producto</label><select id="product-category" value={selected} onChange={(event) => { setSelected(event.target.value); if (event.target.value === (product.category_id ?? "")) setServerChanged(false); }} disabled={busy || uncertain}>
          <option value="" disabled={product.is_active}>Sin categoría</option>
          {selected && !categories.some((item) => item.id === selected) && <option value={selected}>Categoría actual — cargá más categorías para ver su nombre</option>}
          {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
        </select>
        {selected !== (product.category_id ?? "") && <p className="pending-edit" role="status">Selección local sin guardar.</p>}
        {serverChanged && selected !== (product.category_id ?? "") && <div className="notice" role="status"><p>La categoría guardada cambió durante tu edición. Revisá el valor actual antes de guardar.</p><button type="button" className="button-quiet" onClick={() => { setSelected(product.category_id ?? ""); setServerChanged(false); }}>Usar valor guardado</button><button type="button" className="button-quiet" onClick={() => setServerChanged(false)}>Conservar mi selección</button></div>}
        {loading && <p role="status">Consultando categorías…</p>}{categoryError && <p role="alert">{categoryError}</p>}
        {next && <button type="button" disabled={loading || busy} onClick={() => { setLoading(true); setAfter(next); setAttempt((value) => value + 1); }}>Más categorías disponibles</button>}
        <div className="classification-actions"><button type="button" className="button-quiet" disabled={loading || busy} onClick={() => { reads.current.invalidate(); setLoading(true); setAfter(null); setAttempt((value) => value + 1); }}>Actualizar categorías disponibles</button>
        <button className="button-primary" disabled={busy || uncertain || loading || serverChanged || (product.is_active && !selected)}>Guardar categoría</button></div>
        {product.is_active && <p className="caption">Desactivá el producto antes de quitar su categoría.</p>}
      </form>}</div>
      <div><h3>Activación administrativa</h3><p className="muted">Requiere categoría guardada. No cambia precio ni medición.</p>
        {!canActivate ? <p>Sin permiso para cambiar el estado administrativo.</p> : <button disabled={busy || uncertain || (!product.is_active && product.category_id === null)} onClick={() => void mutate("active", { is_active: !product.is_active })}>{product.is_active ? "Desactivar producto" : "Activar producto"}</button>}
        {!product.is_active && !product.category_id && <p className="caption">Asigná una categoría antes de activar.</p>}
      </div>
    </div>
    <p className="caption">Activo no significa publicado, disponible ni vendible.</p><details className="technical-details"><summary>Alcance de esta pantalla</summary><p>Los editores de precios y medición no están incluidos aquí; eso no indica ausencia de esos datos.</p></details>
  </section>;
}
