import { CatalogBrowser } from "@/components/catalog-browser";
import { OperationsShell } from "@/components/operations-shell";

export default function CatalogPage() {
  return <OperationsShell><CatalogBrowser /></OperationsShell>;
}
