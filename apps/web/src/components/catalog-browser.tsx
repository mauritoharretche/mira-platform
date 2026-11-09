"use client";

import Link from "next/link";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Category, CategoryPage, catalogQuery, messageFor, OperatorError, post, ProductPage, ReadGeneration, request } from "@/lib/operations-api";
import { useOperator } from "./operations-shell";

export function CatalogBrowser() {
  const { capabilities } = useOperator();
  const canRead = capabilities.includes("catalog.read_product");
  const canCategories = capabilities.includes("catalog.list_categories");
  const canCreate = capabilities.includes("catalog.create_category");
  const [filter, setFilter] = useState("all");
  const [after, setAfter] = useState<string | null>(null);
  const [page, setPage] = useState<{ key: string; value: ProductPage } | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoryCursor, setCategoryCursor] = useState<string | null>(null);
  const [categoryAfter, setCategoryAfter] = useState<string | null>(null);
  const [categoryAttempt, setCategoryAttempt] = useState(0);
  const [categoryLoading, setCategoryLoading] = useState(true);
  const [categoryError, setCategoryError] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [name, setName] = useState("");
  const [notice, setNotice] = useState("");
  const [createError, setCreateError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const reads = useRef(new ReadGeneration());
  const categoryReads = useRef(new ReadGeneration());
  const alive = useRef(false);
  const key = catalogQuery(filter, after);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    const stream = reads.current;
    const token = stream.begin();
    request<ProductPage>(key, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted && stream.accepts(token)) { setPage({ key, value }); setError(""); }
    }).catch((failure) => { if (!controller.signal.aborted && stream.accepts(token)) setError(messageFor(failure)); });
    return () => { controller.abort(); stream.invalidate(); };
  }, [key, attempt, canRead]);

  useEffect(() => {
    if (!canCategories) return;
    const controller = new AbortController();
    const categoryStream = categoryReads.current;
    const token = categoryStream.begin();
    request<CategoryPage>(`/catalog/categories${categoryAfter ? `?after=${encodeURIComponent(categoryAfter)}` : ""}`, { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted || !categoryStream.accepts(token)) return;
      setCategories((previous) => categoryAfter ? [...previous, ...value.categories.filter((item) => !previous.some((old) => old.id === item.id))] : value.categories);
      setCategoryCursor(value.next_cursor); setCategoryLoading(false); setCategoryError("");
    }).catch((failure) => { if (!controller.signal.aborted && categoryStream.accepts(token)) { setCategoryError(messageFor(failure)); setCategoryLoading(false); } });
    return () => { controller.abort(); categoryStream.invalidate(); };
  }, [categoryAfter, categoryAttempt, canCategories]);

  function selectFilter(value: string) {
    reads.current.invalidate(); setPage(null); setError(""); setFilter(value); setAfter(null);
  }
  function refreshCategories() {
    categoryReads.current.invalidate(); setCategoryLoading(true); setCategoryAfter(null); setCategoryAttempt((value) => value + 1);
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canCreate || busy || uncertain) return;
    if (!name.trim()) { setCreateError("Ingresá un nombre de categoría."); return; }
    setBusy(true); setCreateError(""); setNotice("");
    try {
      const result = await post<Category>("/catalog/categories", { name });
      if (!alive.current) return;
      if (typeof result !== "object" || result === null || typeof result.id !== "string" || !result.id || typeof result.name !== "string" || !result.name) {
        throw new OperatorError("invalid_response", undefined, true);
      }
      setNotice(`Categoría creada: ${result.name}.`); setName("");
      if (canCategories) refreshCategories();
    } catch (failure) {
      if (!alive.current) return;
      setCreateError(messageFor(failure));
      if (failure instanceof OperatorError && failure.uncertain) setUncertain(true);
    } finally { if (alive.current) setBusy(false); }
  }
  const current = page?.key === key ? page.value : null;
  return <>
    <div className="page-heading"><p className="eyebrow">Catálogo operativo</p><h1>Organizá tus productos</h1><p className="muted">Clasificación y estado administrativo. Este entorno no habilita ventas.</p></div>
    <div className="catalog-layout">
      <aside className="panel panel-subtle"><h2>Categorías</h2>
        {!canCategories ? <p>Sin permiso para consultar categorías.</p> : <>
          <label htmlFor="catalog-filter">Filtrar productos</label><select id="catalog-filter" value={filter} onChange={(event) => selectFilter(event.target.value)} disabled={!canRead}>
            <option value="all">Todos los productos</option><option value="uncategorized">Sin categoría</option>
            {filter !== "all" && filter !== "uncategorized" && !categories.some((item) => item.id === filter) && <option value={filter}>Categoría seleccionada — cargá más categorías</option>}
            {categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}
          </select>
          <ul className="category-navigation" aria-label="Categorías cargadas">{categories.map((category) => <li key={category.id}><button className="button-quiet" disabled={!canRead} onClick={() => selectFilter(category.id)}>{category.name}</button></li>)}</ul>
          <p className="caption">Las categorías se cargan por páginas. Cambiar el filtro reinicia el listado.</p>
          {categoryError && <p role="alert">{categoryError}</p>}
          {categoryLoading && <p role="status">Consultando categorías…</p>}
          {categoryCursor && <button className="button-quiet" disabled={categoryLoading} onClick={() => { setCategoryLoading(true); setCategoryAfter(categoryCursor); setCategoryAttempt((value) => value + 1); }}>Más categorías</button>}
          <button className="button-quiet" disabled={categoryLoading} onClick={refreshCategories}>Actualizar categorías</button>
        </>}
        {!canCategories && canRead && <><label htmlFor="catalog-basic-filter">Filtrar productos</label><select id="catalog-basic-filter" value={filter} onChange={(event) => selectFilter(event.target.value)}><option value="all">Todos los productos</option><option value="uncategorized">Sin categoría</option></select></>}
        {canCreate && <form className="category-create" onSubmit={(event) => void create(event)} aria-busy={busy}>
          <h2>Nueva categoría</h2><label htmlFor="category-name">Nombre de categoría</label><input id="category-name" maxLength={255} value={name} onChange={(event) => setName(event.target.value)} required disabled={busy || uncertain} />
          <button className="button-primary" disabled={busy || uncertain}>{busy ? "Creando…" : "Crear categoría"}</button>
          {createError && <p role="alert" className="field-error">{createError}</p>}{notice && <p role="status">{notice}</p>}
          {uncertain && <p className="notice">La creación pudo haberse guardado. Consultá las categorías; no reenvíes esta operación.</p>}
        </form>}
      </aside>
      <section className="panel" aria-labelledby="catalog-products-title"><div className="section-heading"><h2 id="catalog-products-title">Productos</h2><Link href="/operations">Nuevo producto</Link></div>
        {!canRead ? <p role="alert">Tu cuenta no tiene permiso para consultar productos.</p> : <>
          {error && <p role="alert" className="error-box">{error}</p>}
          {!current && !error && <p role="status">Consultando productos…</p>}
          {current && <>{!current.products.length ? <p className="empty-state">No hay productos para este filtro.</p> : <ul className="catalog-list">{current.products.map((product) => <li key={product.id}>
            <Link href={`/operations/products/${product.id}`}>{product.name}</Link><span className="badge">{product.is_active ? "Activo administrativamente" : "Inactivo administrativamente"}</span>
            <p className="caption">{product.category_id ? categories.find((item) => item.id === product.category_id)?.name ?? "Categoría asignada" : "Sin categoría"}</p>
          </li>)}</ul>}
          <div className="catalog-pagination"><button className="button-quiet" onClick={() => { reads.current.invalidate(); setPage(null); setAfter(null); setError(""); setAttempt((value) => value + 1); }}>Volver al inicio / actualizar</button>
            {current.next_cursor && <button onClick={() => { reads.current.invalidate(); setPage(null); setError(""); setAfter(current.next_cursor); }}>Más productos</button>}</div></>}
          {error && <button onClick={() => { setError(""); setAttempt((value) => value + 1); }}>Reintentar consulta</button>}
        </>}
      </section>
    </div>
  </>;
}
