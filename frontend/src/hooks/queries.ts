"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useScope } from "@/hooks/scope";
import { api, type DateRange } from "@/services/api";
import type { ReviewFilters } from "@/types/api";

/** Every analytics query carries the current data scope (real vs demonstration) in its cache key. */
function useScoped() {
  const { demo, ready } = useScope();
  return { scope: { include_demo: demo }, ready };
}

export function useCoverage() {
  const { scope, ready } = useScoped();
  return useQuery({ queryKey: ["coverage", scope], queryFn: () => api.coverage(scope), enabled: ready });
}
export function useOverview(range: DateRange) {
  const { scope, ready } = useScoped();
  return useQuery({ queryKey: ["overview", scope, range], queryFn: () => api.overview(scope, range), enabled: ready });
}
export function usePropertyComparison() {
  const { scope, ready } = useScoped();
  return useQuery({ queryKey: ["comparison", scope], queryFn: () => api.propertyComparison(scope), enabled: ready });
}
export function useProperty(id: string) {
  const { scope, ready } = useScoped();
  return useQuery({ queryKey: ["property", scope, id], queryFn: () => api.property(scope, id), enabled: ready });
}
export function useWeekly(property_id?: string) {
  const { scope, ready } = useScoped();
  return useQuery({ queryKey: ["weekly", scope, property_id], queryFn: () => api.weekly(scope, { property_id }), enabled: ready });
}
export function useTopics(p: DateRange & { property_id?: string } = {}) {
  const { scope, ready } = useScoped();
  return useQuery({ queryKey: ["topics", scope, p], queryFn: () => api.topics(scope, p), enabled: ready });
}
export function useRecommendations(p: { property_id?: string; min_reviews?: number; window_weeks?: number } = {}) {
  const { scope, ready } = useScoped();
  return useQuery({ queryKey: ["recs", scope, p], queryFn: () => api.recommendations(scope, p), enabled: ready });
}
export function useReviews(filters: ReviewFilters) {
  const { scope, ready } = useScoped();
  return useQuery({
    queryKey: ["reviews", scope, filters],
    queryFn: () => api.reviews(scope, filters),
    enabled: ready,
    placeholderData: keepPreviousData,
  });
}
/** Run history; polls every 3 s while a collection or analysis is still running. */
export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => api.runs(),
    refetchInterval: (q) => (q.state.data?.data.some((r) => r.status === "running") ? 3000 : false),
  });
}
export function useAnalysisConfig() {
  return useQuery({ queryKey: ["analysis-config"], queryFn: () => api.analysisConfig() });
}
export function useCollectionStatus() {
  return useQuery({ queryKey: ["collection-status"], queryFn: () => api.collectionStatus() });
}
export function useProperties() {
  return useQuery({ queryKey: ["properties"], queryFn: () => api.properties(), staleTime: 5 * 60_000 });
}
