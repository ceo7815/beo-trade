import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import "./v2.css";

const rubik = localFont({
  src: "../fonts/Rubik-latin.woff2",
  weight: "300 700",
  style: "normal",
  display: "swap",
  variable: "--font-rubik",
  adjustFontFallback: "Arial",
});

const rubikHebrew = localFont({
  src: "../fonts/Rubik-hebrew.woff2",
  weight: "300 700",
  style: "normal",
  display: "swap",
  variable: "--font-rubik-hebrew",
  adjustFontFallback: false,
});

const rubikLatinExt = localFont({
  src: "../fonts/Rubik-latin-ext.woff2",
  weight: "300 700",
  style: "normal",
  display: "swap",
  preload: false,
  adjustFontFallback: false,
  variable: "--font-rubik-ext",
});

export const metadata: Metadata = {
  title: "Beo-Trade",
  description: "דסק חי להמלצות אופציות ולמעקב רווח. חשבון Alpaca Paper.",
  appleWebApp: { capable: true, title: "Beo-Trade", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#07080a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="he" dir="rtl" className={`${rubik.variable} ${rubikHebrew.variable} ${rubikLatinExt.variable}`}>
      <body className="antialiased">{children}</body>
    </html>
  );
}
