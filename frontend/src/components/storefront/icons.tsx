import type { SVGProps } from "react";

export type IconName = "search" | "menu" | "heart" | "compare" | "cart" | "user" | "pin" | "arrow" | "close" | "chevron" | "filter" | "grid" | "list" | "spark" | "phone" | "check" | "plus" | "minus" | "external" | "box" | "bolt" | "cable" | "lamp" | "socket" | "shield" | "tool" | "panel" | "camera" | "power" | "layers";
export function Icon({ name, size = 20, ...props }: SVGProps<SVGSVGElement> & { name: IconName; size?: number }) {
  const paths: Record<IconName, React.ReactNode> = {
    search: <><circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 5 5"/></>,
    menu: <><path d="M3 6h18M3 12h18M3 18h18"/></>,
    heart: <path d="M20.5 8.7c0 4.4-8.5 10.1-8.5 10.1S3.5 13.1 3.5 8.7a4.5 4.5 0 0 1 8.5-2 4.5 4.5 0 0 1 8.5 2Z"/>,
    compare: <><path d="M7 4v16M17 4v16M3 8h8M13 16h8"/><path d="m3 8 4 5 4-5m2 8 4 5 4-5"/></>,
    cart: <><path d="M2.5 4h2l2.2 10.6a2 2 0 0 0 2 1.6h9.5a2 2 0 0 0 2-1.6L22 7H5.1"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/></>,
    user: <><circle cx="12" cy="8" r="4"/><path d="M4.5 21c.5-4.2 3-6 7.5-6s7 1.8 7.5 6"/></>,
    pin: <><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z"/><circle cx="12" cy="10" r="2.2"/></>,
    arrow: <><path d="M4 12h16m-6-6 6 6-6 6"/></>,
    close: <path d="M5 5 19 19M19 5 5 19"/>,
    chevron: <path d="m6 9 6 6 6-6"/>,
    filter: <><path d="M3 6h18M6 12h12M9 18h6"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    list: <><path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/></>,
    spark: <><path d="m12 2 1.7 6.3L20 10l-6.3 1.7L12 18l-1.7-6.3L4 10l6.3-1.7L12 2ZM19 17l.7 1.3L21 19l-1.3.7L19 21l-.7-1.3L17 19l1.3-.7L19 17Z"/></>,
    phone: <path d="M7 3 4 5c-1 7 8 16 15 15l2-3-5-4-2 2c-2-1-4-3-5-5l2-2-4-5Z"/>,
    check: <path d="m4 12 5 5L20 6"/>,
    plus: <path d="M12 4v16M4 12h16"/>,
    minus: <path d="M4 12h16"/>,
    external: <><path d="M14 4h6v6M20 4l-9 9"/><path d="M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5"/></>,
    box: <><path d="m12 2 9 5-9 5-9-5 9-5Zm-9 5v10l9 5 9-5V7M12 12v10"/></>,
    bolt: <path d="m13 2-9 12h7l-1 8 10-12h-7l0-8Z"/>,
    cable: <><path d="M3 9h5v6H3V9Zm13 0h5v6h-5V9ZM8 12h8M5.5 9V5M18.5 9V5"/></>,
    lamp: <><path d="M8 15c0-2-3-3-3-7a7 7 0 0 1 14 0c0 4-3 5-3 7H8Zm1 3h6m-5 3h4"/></>,
    socket: <><rect x="4" y="3" width="16" height="18" rx="3"/><circle cx="9" cy="10" r="1"/><circle cx="15" cy="10" r="1"/><path d="M9 16c2 1 4 1 6 0"/></>,
    shield: <><path d="m12 2 8 3v6c0 5-3 8-8 11-5-3-8-6-8-11V5l8-3Z"/><path d="m8 12 3 3 5-6"/></>,
    tool: <><path d="M15 4a5 5 0 0 0-6 6L3 16l5 5 6-6a5 5 0 0 0 6-6l-4 2-3-3 2-4Z"/></>,
    panel: <><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M9 9v12M14 14h3"/></>,
    camera: <><rect x="3" y="6" width="18" height="13" rx="2"/><circle cx="12" cy="12.5" r="3"/><path d="m7 6 1-3h8l1 3"/></>,
    power: <><path d="M12 2v10M6 5a9 9 0 1 0 12 0"/></>,
    layers: <><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
