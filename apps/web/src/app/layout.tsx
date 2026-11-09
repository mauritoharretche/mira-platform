import type { Metadata } from "next";

import "./styles.css";

export const metadata: Metadata = {
  title: "MIRA Platform",
  description: "MIRA · Operaciones internas",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es-AR">
      <body>{children}</body>
    </html>
  );
}
