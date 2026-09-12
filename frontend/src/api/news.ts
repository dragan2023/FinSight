import { request } from "./client";

export interface TickerLink {
  thscode: string;
  name: string;
  confidence: string;
}

export interface FeedInfo {
  id: string;
  name: string;
  url: string;
  enabled: boolean;
  last_fetch_at: string | null;
  last_success_at: string | null;
  last_error: string;
  consecutive_failures: number;
  item_count: number;
  created_at: string;
}

export interface NewsItem {
  id: string;
  feed_id: string;
  feed_name: string;
  title: string;
  summary: string;
  link: string;
  published_ms: number | null;
  fetched_at: string;
  tickers: TickerLink[];
}

export interface FeedRefreshOutcome {
  feed_id: string;
  name: string;
  status: string;
  added: number;
  message: string;
}

export interface RefreshSummary {
  outcomes: FeedRefreshOutcome[];
  added: number;
}

export interface OfficialNewsItem {
  id: string;
  feed_name: string;
  title: string;
  summary: string;
  link: string;
  published_ms: number | null;
  fetched_at: string;
  channel: string;
  tickers: TickerLink[];
}

export interface RssSettings {
  poll_enabled: boolean;
  interval_minutes: number;
}

export interface NewsFilter {
  thscode?: string | null;
  feed_id?: string | null;
}

export const getFeeds = () =>
  request<{ feeds: FeedInfo[] }>({ url: "/rss/feeds" });

export const addFeed = (data: { name: string; url: string }) =>
  request<{ feed: FeedInfo }>({ url: "/rss/feeds", method: "POST", data });

export const addRecommendedFeeds = () =>
  request<{ feeds: FeedInfo[] }>({ url: "/rss/feeds/recommended", method: "POST" });

export const updateFeed = (
  id: string,
  data: { name?: string; enabled?: boolean },
) => request<{ feed: FeedInfo }>({ url: `/rss/feeds/${id}`, method: "PUT", data });

export const deleteFeed = (id: string) =>
  request<{ deleted: boolean }>({ url: `/rss/feeds/${id}`, method: "DELETE" });

export const refreshFeeds = (id?: string) =>
  request<{ summary: RefreshSummary }>({
    url: id ? `/rss/feeds/${id}/refresh` : "/rss/refresh",
    method: "POST",
  });

export const getNews = (filter: NewsFilter) =>
  request<{ items: NewsItem[]; total: number }>({
    url: "/rss/items",
    params: {
      thscode: filter.thscode ?? undefined,
      feed_id: filter.feed_id ?? undefined,
      limit: 100,
    },
  });

export const fetchOfficialNews = () =>
  request<{ items: OfficialNewsItem[]; funds: number; stocks: number; warnings: string[]; fetched_at: string }>({
    url: "/rss/official-news",
    method: "POST",
    data: {},
  });

export const getRssSettings = () =>
  request<{ settings: RssSettings }>({ url: "/rss/settings" });

export const updateRssSettings = (data: RssSettings) =>
  request<{ settings: RssSettings }>({ url: "/rss/settings", method: "PUT", data });
