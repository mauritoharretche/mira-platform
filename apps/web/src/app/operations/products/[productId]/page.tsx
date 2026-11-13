import { OperationsShell } from "@/components/operations-shell";
import { ProductWorkspace } from "@/components/product-workspace";
import { canonicalUuid, catalogHref, CatalogSearchParams, parseCatalogViewState, productHref } from "@/lib/catalog-view-state";
import { redirect } from "next/navigation";

export default async function ProductPage({ params, searchParams }: {
  params: Promise<{ productId: string }>;
  searchParams: Promise<CatalogSearchParams>;
}) {
  const { productId } = await params;
  const parsed = parseCatalogViewState(await searchParams);
  if (parsed.needsRedirect && canonicalUuid(productId)) redirect(productHref(productId, parsed.state));
  return <OperationsShell><ProductWorkspace key={productId} productId={productId} returnHref={catalogHref(parsed.state)} /></OperationsShell>;
}
