import { CatalogBrowser } from "@/components/catalog-browser";
import { OperationsShell } from "@/components/operations-shell";
import { catalogHref, CatalogSearchParams, parseCatalogViewState } from "@/lib/catalog-view-state";
import { redirect } from "next/navigation";

export default async function CatalogPage({ searchParams }: { searchParams: Promise<CatalogSearchParams> }) {
  const parsed = parseCatalogViewState(await searchParams);
  if (parsed.needsRedirect) redirect(catalogHref(parsed.state));
  return <OperationsShell><CatalogBrowser view={parsed.state} /></OperationsShell>;
}
