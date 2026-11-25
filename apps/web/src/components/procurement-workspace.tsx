"use client";

import { quantityBase } from "@/lib/quantities";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useOperator } from "@/components/operations-shell";
import { OperatorError, request, Stock } from "@/lib/operations-api";
import { Location, Lot, lots } from "@/lib/lot-api";
import { DraftLine, Order, ReceiptDraftLine, Supplier, VariantReference,
  procurement, procurementFailure, validOrderLine, validReceiptLine } from "@/lib/procurement-api";

const emptyLine = (): DraftLine => ({ variant_id: "", quantity: "1", unit: "EACH", unit_cost_ars: "0.00" });
const capability = {
  suppliers: "procurement.read_supplier", manage: "procurement.manage_supplier",
  orders: "procurement.read_purchase_order", create: "procurement.create_purchase_order",
  edit: "procurement.edit_purchase_order", confirm: "procurement.confirm_purchase_order",
  receive: "procurement.receive_purchase_order", variants: "catalog.read_product",
};

export function ProcurementWorkspace() {
  const { capabilities } = useOperator();
  const can = (name: string) => capabilities.includes(name);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [supplierSearch, setSupplierSearch] = useState("");
  const [supplierName, setSupplierName] = useState("");
  const [selectedSupplier, setSelectedSupplier] = useState<Supplier | null>(null);
  const [supplierActive, setSupplierActive] = useState(true);
  const [orders, setOrders] = useState<Order[]>([]);
  const [order, setOrder] = useState<Order | null>(null);
  const [draftSupplier, setDraftSupplier] = useState("");
  const [lines, setLines] = useState<DraftLine[]>([emptyLine()]);
  const [variants, setVariants] = useState<VariantReference[]>([]);
  const [variantSearch, setVariantSearch] = useState("");
  const [receiptLines, setReceiptLines] = useState<ReceiptDraftLine[]>([]);
  const [stocks, setStocks] = useState<Record<string, Stock | null>>({});
  const [lotOptions, setLotOptions] = useState<Record<string, Lot[]>>({});
  const [locations, setLocations] = useState<Location[]>([]);
  const [receiptOperation, setReceiptOperation] = useState<string | null>(null);
  const [receiptUncertain, setReceiptUncertain] = useState(false);
  const [writeBlocked, setWriteBlocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const refreshSuppliers = useCallback(async (search = "") => {
    const result = await procurement.suppliers(search);
    setSuppliers(result.suppliers);
  }, []);
  const refreshOrders = useCallback(async () => {
    const result = await procurement.orders();
    setOrders(result.orders);
  }, []);
  useEffect(() => {
    if (capabilities.includes(capability.suppliers)) void procurement.suppliers().then((result) => setSuppliers(result.suppliers))
      .catch((failure) => setError(procurementFailure(failure)));
    if (capabilities.includes(capability.orders)) void procurement.orders().then((result) => setOrders(result.orders))
      .catch((failure) => setError(procurementFailure(failure)));
    if (capabilities.includes(capability.variants)) void procurement.variants("").then((result) => setVariants(result.variants))
      .catch((failure) => setError(procurementFailure(failure)));
  }, [capabilities]);

  function adoptOrder(next: Order) {
    setOrder(next);
    setDraftSupplier(next.supplier_id);
    setLines(next.lines.map((line) => ({ variant_id: line.variant_id, quantity: line.quantity,
      unit: line.unit, cost_basis: line.cost_basis, unit_cost_ars: line.unit_cost_ars })));
    setReceiptLines(next.lines.filter((line) => (quantityBase(line.pending_quantity,line.unit) ?? 0n)>0n).map((line) => ({
      order_line_id: line.id, unit: line.unit, delivered_quantity: "", accepted_quantity: "", difference_reason: null,
      expected_stock_version: null,
    })));
  }
  async function readOrder(id: string) {
    const result = await procurement.order(id);
    adoptOrder(result.order);
    return result.order;
  }

  async function act(action: () => Promise<void>) {
    if (busy || writeBlocked) return;
    setBusy(true); setError(""); setStatus("");
    try { await action(); }
    catch (failure) {
      if (failure instanceof OperatorError && failure.uncertain) setWriteBlocked(true);
      setError(procurementFailure(failure));
    } finally { setBusy(false); }
  }

  async function searchSuppliers(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    try { await refreshSuppliers(supplierSearch); } catch (failure) { setError(procurementFailure(failure)); }
  }

  async function saveSupplier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await act(async () => {
      const result = selectedSupplier
        ? await procurement.updateSupplier(selectedSupplier, supplierName, supplierActive)
        : await procurement.createSupplier(supplierName);
      setSelectedSupplier(result.supplier); setSupplierName(result.supplier.name); setSupplierActive(result.supplier.active);
      setStatus(selectedSupplier ? "Proveedor actualizado." : "Proveedor creado.");
      await refreshSuppliers(supplierSearch).catch(() => setError("Proveedor guardado; no pudimos actualizar el listado. Consultalo nuevamente."));
    });
  }

  function changeLine(index: number, patch: Partial<DraftLine>) {
    setLines((current) => current.map((line, position) => position === index ? { ...line, ...patch } : line));
  }
  async function searchVariants() {
    setError("");
    try { setVariants((await procurement.variants(variantSearch)).variants); }
    catch (failure) { setError(procurementFailure(failure)); }
  }
  async function saveOrder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draftSupplier || !lines.length || lines.some((line) => !validOrderLine(line))
        || new Set(lines.map((line) => line.variant_id)).size !== lines.length) {
      setError("Seleccioná proveedor y variantes distintas; ingresá cantidades EACH y costos ARS válidos."); return;
    }
    await act(async () => {
      const result = order?.status === "DRAFT"
        ? await procurement.updateOrder(order, draftSupplier, lines)
        : await procurement.createOrder(draftSupplier, lines);
      adoptOrder(result.order);
      setStatus(order?.status === "DRAFT" ? "Borrador actualizado." : "Orden creada en borrador.");
      await refreshOrders().catch(() => setError("Orden guardada; no pudimos actualizar el listado. Consultalo nuevamente."));
    });
  }
  async function confirm() {
    if (!order || order.status !== "DRAFT") return;
    await act(async () => {
      const result = await procurement.confirm(order);
      adoptOrder(result.order); setStatus("Orden confirmada.");
      await refreshOrders().catch(() => setError("Orden confirmada; no pudimos actualizar el listado."));
    });
  }
  function changeReceiptLine(id: string, patch: Partial<ReceiptDraftLine>) {
    setReceiptLines((current) => current.map((line) => line.order_line_id === id ? { ...line, ...patch } : line));
  }
  async function loadStockVersion(variantId: string, lineId: string) {
    try {
      const result = await request<{ stock: Stock | null }>(`/inventory/variants/${variantId}/stock`);
      setStocks((current) => ({ ...current, [variantId]: result.stock }));
      changeReceiptLine(lineId, { expected_stock_version: result.stock?.version ?? null });
      setStatus(result.stock ? "Versión de stock consultada." : "Sin stock previo: se registrará la primera recepción.");
    } catch (failure) { setError(procurementFailure(failure)); }
  }
  async function inspectStock(variantId: string) {
    try {
      const result = await request<{ stock: Stock | null }>(`/inventory/variants/${variantId}/stock`);
      setStocks((current) => ({ ...current, [variantId]: result.stock }));
    } catch (failure) { setError(procurementFailure(failure)); }
  }
  async function inspectLots(variantId: string) {
    try {
      const [state, places] = await Promise.all([lots.state(variantId), lots.locations()]);
      setLotOptions((current) => ({ ...current, [variantId]: state.lots }));
      setLocations(places.locations);
      setStatus("Lotes y ubicaciones consultados. La asignación es opcional; sin ella el stock queda sin asignar.");
    } catch (failure) { setError(procurementFailure(failure)); }
  }
  async function receive(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!order || !receiptLines.length) return;
    const submitted = receiptLines.filter((line) => line.delivered_quantity !== "");
    if (!submitted.length || submitted.some((line) => !validReceiptLine(line))) {
      setError("Ingresá cantidades válidas; explicá toda diferencia y consultá la versión de stock cuando corresponda."); return;
    }
    const operation = crypto.randomUUID();
    setReceiptOperation(operation);
    await act(async () => {
      try {
        const result = await procurement.receive(order, operation, submitted);
        setReceiptUncertain(false); adoptOrder(result.order);
        setStatus("Recepción registrada. Consultá los movimientos desde la ficha de cada variante.");
        await refreshOrders().catch(() => setError("Recepción registrada; no pudimos actualizar el listado."));
      } catch (failure) {
        if (failure instanceof OperatorError && failure.uncertain) setReceiptUncertain(true);
        throw failure;
      }
    });
  }
  async function inspectReceipt() {
    if (!receiptOperation) return;
    try {
      const result = await procurement.receiptByOperation(receiptOperation);
      setStatus(`Recepción ${result.receipt.id} registrada. Consultá la orden y el stock.`);
      setReceiptUncertain(false); setWriteBlocked(false);
      if (order) await readOrder(order.id);
      await refreshOrders();
    } catch (failure) {
      setError(failure instanceof OperatorError && failure.code === "not_found"
        ? "La operación no figura registrada. El resultado sigue incierto; no reenvíes la escritura."
        : procurementFailure(failure));
    }
  }

  return <div className="procurement-workspace">
    <div className="page-heading"><p className="eyebrow">Operaciones · Abastecimiento</p><h1>Compras y abastecimiento</h1>
      <p className="muted">Proveedores, órdenes y recepciones de mercadería en unidades EACH.</p></div>
    {status && <p role="status" className="success-box">{status}</p>}
    {error && <p role="alert" className="error-box">{error}</p>}
    {writeBlocked && <p role="alert" className="notice">Escrituras detenidas por un resultado incierto. Consultá la operación antes de continuar.</p>}
    {receiptUncertain && receiptOperation && <div className="panel"><h2>Verificar recepción incierta</h2>
      <p>Identidad de operación: <code>{receiptOperation}</code></p>
      <button type="button" onClick={() => void inspectReceipt()}>Consultar operación</button></div>}

    <section className="panel" aria-labelledby="proc-suppliers"><h2 id="proc-suppliers">Proveedores</h2>
      {can(capability.suppliers) ? <><form onSubmit={(event) => void searchSuppliers(event)} className="proc-inline">
        <label htmlFor="supplier-search">Buscar proveedor</label><input id="supplier-search" value={supplierSearch} onChange={(event) => setSupplierSearch(event.target.value)} maxLength={120} />
        <button>Buscar</button></form>
        <ul className="proc-list">{suppliers.map((item) => <li key={item.id}><button type="button" className="button-quiet" onClick={() => {
          setSelectedSupplier(item); setSupplierName(item.name); setSupplierActive(item.active);
        }}>{item.name} · {item.active ? "Activo" : "Inactivo"}</button></li>)}</ul>
        {suppliers.length === 0 && <p className="empty-state">No hay proveedores para esta búsqueda.</p>}</>
        : <p className="muted">No tenés permiso para consultar proveedores.</p>}
      {can(capability.manage) && <form onSubmit={(event) => void saveSupplier(event)} className="proc-form">
        <h3>{selectedSupplier ? "Editar proveedor" : "Crear proveedor"}</h3>
        <label htmlFor="supplier-name">Nombre ficticio</label><input id="supplier-name" value={supplierName} onChange={(event) => setSupplierName(event.target.value)} required maxLength={120} />
        {selectedSupplier && <label className="proc-check"><input type="checkbox" checked={supplierActive} onChange={(event) => setSupplierActive(event.target.checked)} /> Activo</label>}
        <div className="proc-actions"><button className="button-primary" disabled={busy || writeBlocked}>{selectedSupplier ? "Guardar proveedor" : "Crear proveedor"}</button>
          {selectedSupplier && <button type="button" onClick={() => { setSelectedSupplier(null); setSupplierName(""); setSupplierActive(true); }}>Nuevo proveedor</button>}</div>
      </form>}
    </section>

    <section className="panel" aria-labelledby="proc-orders"><h2 id="proc-orders">Órdenes de compra</h2>
      {can(capability.orders) ? <><button type="button" onClick={() => void refreshOrders().catch((failure) => setError(procurementFailure(failure)))}>Actualizar listado</button>
        <ul className="proc-list">{orders.map((item) => <li key={item.id}><button type="button" className="button-quiet" onClick={() => void readOrder(item.id).catch((failure) => setError(procurementFailure(failure)))}>
          {item.supplier_name} · {item.status} · {item.id.slice(0, 8)}</button></li>)}</ul>
        {orders.length === 0 && <p className="empty-state">Todavía no hay órdenes.</p>}</>
        : <p className="muted">No tenés permiso para consultar órdenes.</p>}
      {can(capability.create) && order && <button type="button" onClick={() => { setOrder(null); setDraftSupplier(""); setLines([emptyLine()]); }}>Nueva orden</button>}
      {(can(capability.create) && !order || can(capability.edit) && order?.status === "DRAFT") && <form onSubmit={(event) => void saveOrder(event)} className="proc-form">
        <h3>{order?.status === "DRAFT" ? "Editar borrador" : "Nueva orden"}</h3>
        <label htmlFor="order-supplier">Proveedor activo</label><select id="order-supplier" value={draftSupplier} onChange={(event) => setDraftSupplier(event.target.value)} required>
          <option value="">Seleccionar</option>{suppliers.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        {can(capability.variants) && <div className="proc-inline" role="group" aria-label="Buscar variantes">
          <label htmlFor="variant-search">Buscar variante por nombre o SKU</label><input id="variant-search" value={variantSearch} onChange={(event) => setVariantSearch(event.target.value)} maxLength={120} />
          <button type="button" onClick={() => void searchVariants()}>Buscar variantes</button></div>}
        {lines.map((line, index) => <fieldset key={index} className="proc-line"><legend>Renglón {index + 1}</legend>
          <label htmlFor={`variant-${index}`}>Variante</label><select id={`variant-${index}`} value={line.variant_id} onChange={(event) => changeLine(index, { variant_id: event.target.value })} required>
            <option value="">Seleccionar variante</option>{variants.map((item) => <option key={item.id} value={item.id}>{item.product_name} · {item.label} · {item.sku}</option>)}</select>
          <label htmlFor={`unit-${index}`}>Unidad contable</label><select id={`unit-${index}`} value={line.unit} onChange={(event)=>changeLine(index,{unit:event.target.value as "EACH" | "G"})}><option value="EACH">EACH</option><option value="G">G — precisión 0,001 g</option></select>
          <label htmlFor={`basis-${index}`}>Base del costo</label><select id={`basis-${index}`} value={line.cost_basis ?? "PER_VARIANT"} onChange={(event)=>changeLine(index,{cost_basis:event.target.value as "PER_VARIANT" | "PER_KG"})}><option value="PER_VARIANT">Por unidad EACH</option><option value="PER_KG">Por kilogramo</option></select>
          <label htmlFor={`qty-${index}`}>Cantidad {line.unit}</label><input id={`qty-${index}`} inputMode="numeric" value={line.quantity} onChange={(event) => changeLine(index, { quantity: event.target.value })} required />
          <label htmlFor={`cost-${index}`}>Costo unitario ARS</label><input id={`cost-${index}`} inputMode="decimal" value={line.unit_cost_ars} onChange={(event) => changeLine(index, { unit_cost_ars: event.target.value })} required />
          {lines.length > 1 && <button type="button" onClick={() => setLines((current) => current.filter((unused, position) => position !== index))}>Quitar renglón</button>}
        </fieldset>)}
        <div className="proc-actions"><button type="button" onClick={() => setLines((current) => [...current, emptyLine()])} disabled={lines.length >= 30}>Agregar variante</button>
          <button className="button-primary" disabled={busy || writeBlocked}>{order?.status === "DRAFT" ? "Guardar borrador" : "Crear orden"}</button>
          <button type="button" onClick={() => { setOrder(null); setDraftSupplier(""); setLines([emptyLine()]); }}>Nueva orden</button></div>
      </form>}
    </section>

    {order && can(capability.orders) && <section className="panel" aria-labelledby="proc-order-detail"><h2 id="proc-order-detail">Detalle de orden</h2>
      <p><strong>{order.supplier_name}</strong> · <span className="badge">{order.status}</span></p>
      <p className="identifier">Orden {order.id} · Revisión {order.version}</p>
      <div className="table-wrap"><table><thead><tr><th>Variante</th><th>Pedida</th><th>Recibida</th><th>Pendiente</th><th>Costo ARS</th><th>Inventario</th></tr></thead>
        <tbody>{order.lines.map((line) => <tr key={line.id}><td>{line.product_name} · {line.variant_label}<br /><small>SKU {line.variant_sku}</small></td>
          <td>{line.quantity}</td><td>{line.received_quantity}</td><td>{line.pending_quantity} {line.unit}</td><td>{line.unit_cost_ars} / {line.cost_basis === "PER_KG" ? "KG" : "EACH"}</td>
          <td>{can("inventory.read_stock") ? <><button type="button" onClick={() => void inspectStock(line.variant_id)}>Consultar stock</button>
            <span className="proc-stock-result">{line.variant_id in stocks ? `${stocks[line.variant_id]?.on_hand ?? "Sin posición"} ${stocks[line.variant_id]?.unit ?? ""}` : "Pendiente de consulta"}</span></> : "Sin permiso"}</td></tr>)}</tbody></table></div>
      <p>Total previsto: <strong>ARS {order.total_cost_ars}</strong></p>
      <button type="button" onClick={() => void readOrder(order.id).catch((failure) => setError(procurementFailure(failure)))}>Consultar estado vigente</button>
      {order.status === "DRAFT" && can(capability.confirm) && <button type="button" className="button-primary" disabled={busy || writeBlocked} onClick={() => void confirm()}>Confirmar orden</button>}
      <h3>Historial</h3><ol>{order.history?.map((item, index) => <li key={`${item.version}-${index}`}>{item.action} · revisión {item.version} · {item.occurred_at}</li>)}</ol>
      <h3>Recepciones</h3><ol>{order.receipts?.map((item) => <li key={item.id}>Recepción {item.id} · {item.recorded_at}
        <ul>{item.lines.map((line) => <li key={line.order_line_id}>
          {order.lines.find((source) => source.id === line.order_line_id)?.variant_sku} · entregado {line.delivered_quantity}, aceptado {line.accepted_quantity} {line.unit ?? "EACH"}
          {line.difference_reason && <> · diferencia: {line.difference_reason}</>}
          {line.stock_movement_id && <> · movimiento de stock {line.stock_movement_id}</>}
          {line.allocations === null && <> · asignaciones: sin permiso de lectura</>}
          {line.allocations?.map((allocation) => <span key={`${allocation.lot_id}-${allocation.location_id}`}>
            {" · "}lote {allocation.lot_id}, ubicación {allocation.location_id}: {allocation.quantity} {line.unit ?? "EACH"}</span>)}
        </li>)}</ul></li>)}</ol>
      {(order.status === "CONFIRMED" || order.status === "PARTIAL") && can(capability.receive) && <form onSubmit={(event) => void receive(event)} className="proc-form">
        <h3>Registrar recepción</h3><p className="muted">Ingresá lo entregado y lo aceptado. Si hay diferencias, explicalas sin borrar la historia.</p>
        {receiptLines.map((item) => {
          const source = order.lines.find((line) => line.id === item.order_line_id)!;
          return <fieldset key={item.order_line_id} className="proc-line"><legend>{source.variant_label} · SKU {source.variant_sku} · pendiente {source.pending_quantity} {source.unit}</legend>
            <label htmlFor={`delivered-${source.id}`}>Cantidad entregada {source.unit}</label><input id={`delivered-${source.id}`} inputMode="numeric" value={item.delivered_quantity}
              onChange={(event) => changeReceiptLine(source.id, { delivered_quantity: event.target.value })} />
            <label htmlFor={`accepted-${source.id}`}>Cantidad aceptada {source.unit}</label><input id={`accepted-${source.id}`} inputMode="numeric" value={item.accepted_quantity}
              onChange={(event) => changeReceiptLine(source.id, { accepted_quantity: event.target.value })} />
            <label htmlFor={`difference-${source.id}`}>Motivo de diferencia, si corresponde</label><input id={`difference-${source.id}`} value={item.difference_reason ?? ""}
              onChange={(event) => changeReceiptLine(source.id, { difference_reason: event.target.value || null })} maxLength={255} />
            <p className="caption">Versión de stock: {item.expected_stock_version ?? "sin stock previo"}</p>
            <button type="button" onClick={() => void loadStockVersion(source.variant_id, source.id)}>Consultar versión de stock</button>
            {can("inventory.read_stock_lots") && can("inventory.allocate_stock_lots") && <>
              <button type="button" onClick={() => void inspectLots(source.variant_id)}>Consultar lotes y ubicaciones</button>
              <label htmlFor={`receipt-lot-${source.id}`}>Lote de lo aceptado, si corresponde</label>
              <select id={`receipt-lot-${source.id}`} value={item.lot_id ?? ""} onChange={(event) => changeReceiptLine(source.id, { lot_id: event.target.value })}>
                <option value="">Sin asignación de lote</option>{(lotOptions[source.variant_id] ?? []).map((lot) =>
                  <option key={lot.id} value={lot.id}>{lot.code} · {lot.status}{lot.expired ? " · vencido" : ""}</option>)}</select>
              <label htmlFor={`receipt-location-${source.id}`}>Ubicación de lo aceptado, si corresponde</label>
              <select id={`receipt-location-${source.id}`} value={item.location_id ?? ""} onChange={(event) => changeReceiptLine(source.id, { location_id: event.target.value })}>
                <option value="">Sin asignación de ubicación</option>{locations.map((location) =>
                  <option key={location.id} value={location.id}>{location.code} · {location.name}</option>)}</select>
            </>}
          </fieldset>;
        })}
        <button className="button-primary" disabled={busy || writeBlocked}>Registrar recepción</button>
      </form>}
    </section>}
  </div>;
}
