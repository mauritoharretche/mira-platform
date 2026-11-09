import { OperationsShell } from "@/components/operations-shell";
import { StockPanel } from "@/components/stock-panel";

export default async function ProductPage({ params }: { params: Promise<{ productId: string }> }) {
  const { productId } = await params;
  return <OperationsShell><StockPanel key={productId} productId={productId} /></OperationsShell>;
}
