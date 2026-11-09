"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { OperationsShell } from "@/components/operations-shell";
import { ProductCreateForm } from "@/components/product-create-form";

export default function OperationsPage() {
  const router = useRouter();
  const [error, setError] = useState("");
  function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = String(new FormData(event.currentTarget).get("product_id") ?? "").trim();
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) return setError("Ingresá un UUID de producto válido.");
    setError("");
    router.push(`/operations/products/${encodeURIComponent(value)}`);
  }
  return <OperationsShell>
    <div className="page-heading"><p className="eyebrow">Catálogo e inventario</p><h1>Prepará tu primera recepción</h1><p className="muted">Un recorrido simple para registrar identidad y existencia física.</p></div>
    <div className="workspace-grid"><ProductCreateForm /><aside className="panel panel-subtle"><h2>Retomar un producto</h2><p>Si ya tenés su identificador, abrilo para consultar su stock.</p><form onSubmit={open}>
      <label htmlFor="product-id">UUID del producto</label><input id="product-id" name="product_id" required autoComplete="off" aria-describedby={error ? "lookup-error" : undefined} />
      {error && <p id="lookup-error" role="alert" className="field-error">{error}</p>}<button>Abrir producto</button>
    </form><p className="caption">Esta primera versión abre productos por ID; no incluye búsqueda general.</p></aside></div>
  </OperationsShell>;
}
