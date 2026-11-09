import { OperationsShell } from "@/components/operations-shell";
import { ProductWorkspace } from "@/components/product-workspace";

export default async function ProductPage({ params }: { params: Promise<{ productId: string }> }) {
  const { productId } = await params;
  return <OperationsShell><ProductWorkspace key={productId} productId={productId} /></OperationsShell>;
}
