import "./globals.css";
import type { Metadata } from "next";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "ICICI Direct Options Trading Terminal",
  description: "Enterprise options trading platform powered by microservices architecture and SQLite-first durability",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark h-full">
      <body className="h-full bg-[#0a0e17] text-slate-100 flex flex-col font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

