import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ЭлектроКомплект — каталог и помощник по подбору",
  description: "Каталог электротехники, сравнение товаров и подборка с подтверждением корзины",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru"><body>{children}</body></html>;
}
