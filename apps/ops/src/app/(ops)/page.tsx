import { redirect } from "next/navigation";

// Early ops builds put the knowledge list at `/`. Some browsers still serve that
// cached document, so `/` only redirects and the real dashboard lives at /overview.
export default function HomePage() {
  redirect("/overview");
}
