import type { Metadata } from "next";
import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { ReviewExplorer } from "./explorer";

export const metadata: Metadata = { title: "Review Explorer" };

export default function ReviewsPage() {
  // useSearchParams (filters live in the URL so a view can be shared) needs a Suspense boundary.
  return (
    <Suspense fallback={<Loading what="reviews" height="h-64" />}>
      <ReviewExplorer />
    </Suspense>
  );
}
