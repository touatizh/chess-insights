import { useEffect } from "react";

export interface DocumentMeta {
  title?: string;
  description?: string;
  /** Absolute URL to the 1200×630 share image. */
  image?: string;
  /** Canonical page URL for og:url / twitter. */
  url?: string;
}

function setMeta(selector: string, attr: "name" | "property", key: string, content: string): void {
  let el = document.head.querySelector<HTMLMetaElement>(selector);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

/**
 * Inject per-report Open Graph / Twitter meta so a pasted /report/{username}
 * link renders the styled share card (Phase 4b growth loop). Restores the
 * document title on unmount; leaves meta tags in place (harmless, overwritten
 * on the next navigation).
 *
 * Note: JS-rendering crawlers (Discord, Slack) pick these up. Strict non-JS
 * crawlers (Twitter/Facebook) rely on the static fallback in index.html until
 * a server-side HTML shim lands in Phase 5.
 */
export function useDocumentMeta(meta: DocumentMeta): void {
  useEffect(() => {
    const previousTitle = document.title;
    if (meta.title) document.title = meta.title;
    if (meta.title) {
      setMeta('meta[property="og:title"]', "property", "og:title", meta.title);
      setMeta('meta[name="twitter:title"]', "name", "twitter:title", meta.title);
    }
    if (meta.description) {
      setMeta('meta[name="description"]', "name", "description", meta.description);
      setMeta('meta[property="og:description"]', "property", "og:description", meta.description);
      setMeta(
        'meta[name="twitter:description"]',
        "name",
        "twitter:description",
        meta.description,
      );
    }
    if (meta.image) {
      setMeta('meta[property="og:image"]', "property", "og:image", meta.image);
      setMeta('meta[name="twitter:image"]', "name", "twitter:image", meta.image);
      setMeta('meta[name="twitter:card"]', "name", "twitter:card", "summary_large_image");
    }
    if (meta.url) {
      setMeta('meta[property="og:url"]', "property", "og:url", meta.url);
    }
    return () => {
      document.title = previousTitle;
    };
  }, [meta.title, meta.description, meta.image, meta.url]);
}
