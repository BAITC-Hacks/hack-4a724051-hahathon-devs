import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ЭКТ — помощник по закупкам",
  description: "Поиск электротехнических товаров и подготовка корзины",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru"><body>{children}</body></html>;
}
