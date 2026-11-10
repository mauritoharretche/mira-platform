"use client";

import Link from "next/link";
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
    <div className="page-heading"><p className="eyebrow">Catálogo · Alta interna</p><h1>Nuevo producto</h1><p className="muted">Creá el producto y su primera presentación. La recepción de stock es un paso independiente.</p></div>
    <p className="back-to-catalog"><Link href="/operations/catalog">Volver al catálogo</Link></p><div className="workspace-grid"><ProductCreateForm /><aside className="panel panel-subtle"><h2>Retomar un producto</h2><p>Si ya tenés su identificador, abrilo para consultar su stock.</p><form onSubmit={open}>
      <label htmlFor="product-id">UUID del producto</label><input id="product-id" name="product_id" required autoComplete="off" aria-describedby={error ? "lookup-error" : undefined} />
      {error && <p id="lookup-error" role="alert" className="field-error">{error}</p>}<button>Abrir producto</button>
    </form><p className="caption">También podés encontrarlo desde el catálogo por categoría.</p></aside></div>
  </OperationsShell>;
}
