import type { Metadata } from "next";

import "./styles.css";

export const metadata: Metadata = {
  title: "MIRA Platform",
  description: "Technical foundation status",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
