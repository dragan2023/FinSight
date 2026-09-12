import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  NotificationOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  Button,
  Empty,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useState } from "react";
import ErrorNotice from "../../components/ErrorNotice";
import { listIdentities } from "../../api/marketData";
import {
  addFeed,
  addRecommendedFeeds,
  deleteFeed,
  getFeeds,
  getNews,
  getRssSettings,
  refreshFeeds,
  updateFeed,
  updateRssSettings,
  fetchOfficialNews,
  type FeedInfo,
  type NewsItem,
  type OfficialNewsItem,
} from "../../api/news";
import { normalizeError } from "../../api/client";

function formatMs(ms: number | null): string {
  if (ms === null) return "时间未知";
  return new Date(ms).toLocaleString("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatIso(value: string | null): string {
  if (!value) return "从未成功";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function FeedStatus({ feed }: { feed: FeedInfo }) {
  if (!feed.enabled) return <Tag>已停用</Tag>;
  if (feed.consecutive_failures > 0) {
    return (
      <Tooltip title={feed.last_error || "最近一次抓取失败"}>
        <Tag color="error">连续失败 {feed.consecutive_failures} 次</Tag>
      </Tooltip>
    );
  }
  return <Tag color="success">启用中</Tag>;
}

type NewsLineItem = Pick<NewsItem, "title" | "summary" | "link" | "published_ms" | "feed_name" | "tickers">;

function NewsLine({ item }: { item: NewsLineItem }) {
  return (
    <li className="news-line">
      {item.link ? (
        <a href={item.link} target="_blank" rel="noreferrer">
          {item.title}
        </a>
      ) : (
        <span>{item.title}</span>
      )}
      <div className="news-line-meta">
        <span>{item.feed_name}</span>
        <span>·</span>
        <span>{formatMs(item.published_ms)}</span>
        {item.tickers.length > 0 && (
          <span className="news-line-tickers">
            {item.tickers.map((link) => (
              <Tag key={link.thscode}>
                {link.name ? `${link.name} ${link.thscode}` : link.thscode}
              </Tag>
            ))}
          </span>
        )}
      </div>
      {item.summary && <p className="news-line-summary">{item.summary}</p>}
    </li>
  );
}

export default function News() {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const [feedFilter, setFeedFilter] = useState<string | null>(null);
  const [codeFilter, setCodeFilter] = useState<string | null>(null);
  const [officialItems, setOfficialItems] = useState<OfficialNewsItem[]>([]);

  const feedsQuery = useQuery({ queryKey: ["rss-feeds"], queryFn: getFeeds });
  const settingsQuery = useQuery({ queryKey: ["rss-settings"], queryFn: getRssSettings });
  const identitiesQuery = useQuery({ queryKey: ["data-identities"], queryFn: listIdentities });
  const itemsQuery = useQuery({
    queryKey: ["rss-items", feedFilter, codeFilter],
    queryFn: () => getNews({ thscode: codeFilter, feed_id: feedFilter }),
  });

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["rss-feeds"] });
    void queryClient.invalidateQueries({ queryKey: ["rss-items"] });
    void queryClient.invalidateQueries({ queryKey: ["rss-settings"] });
  };

  const addMutation = useMutation({
    mutationFn: addFeed,
    onSuccess: () => {
      form.resetFields();
      invalidateAll();
    },
  });
  const recommendedMutation = useMutation({
    mutationFn: addRecommendedFeeds,
    onSuccess: invalidateAll,
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: { id: string; name?: string; enabled?: boolean }) =>
      updateFeed(id, data),
    onSuccess: invalidateAll,
  });
  const deleteMutation = useMutation({
    mutationFn: deleteFeed,
    onSuccess: () => {
      setFeedFilter(null);
      invalidateAll();
    },
  });
  const refreshMutation = useMutation({
    mutationFn: (id?: string) => refreshFeeds(id),
    onSuccess: invalidateAll,
  });
  const settingsMutation = useMutation({
    mutationFn: updateRssSettings,
    onSuccess: invalidateAll,
  });
  const officialMutation = useMutation({
    mutationFn: fetchOfficialNews,
    onSuccess: (data) => setOfficialItems(data.items),
  });

  const feeds = feedsQuery.data?.feeds ?? [];
  const identities = identitiesQuery.data?.identities ?? [];
  const items = itemsQuery.data?.items ?? [];
  const notice = addMutation.error ?? recommendedMutation.error ?? deleteMutation.error ?? refreshMutation.error ?? settingsMutation.error ?? officialMutation.error;
  const noticeText = notice ? normalizeError(notice).message : null;

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">NEWS & SUBSCRIPTIONS</div>
          <h1>新闻订阅</h1>
          <p>免费公开财经媒体订阅：抓取、去重并关联到已确认标的。</p>
        </div>
        <Tag variant="filled">研究参考，不构成投资建议</Tag>
      </div>
      {noticeText && (
        <ErrorNotice
          error={notice}
          retry={() => {
            addMutation.reset();
            refreshMutation.reset();
            officialMutation.reset();
          }}
        />
      )}

      <div className="section-heading">
        <h2>订阅管理</h2>
        <Space wrap>
          <span>只保存标题、摘要与原文链接，全文永不入库</span>
          <Button
            size="small"
            loading={recommendedMutation.isPending}
            onClick={() => recommendedMutation.mutate()}
          >
            添加推荐基础源
          </Button>
        </Space>
      </div>
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => addMutation.mutate(values)}
      >
        <div className="news-add-grid">
          <Form.Item
            name="name"
            label="订阅名称"
            rules={[{ required: true, message: "请输入订阅名称" }]}
          >
            <Input placeholder="如：华尔街见闻" maxLength={60} allowClear />
          </Form.Item>
          <Form.Item
            name="url"
            label="订阅地址（RSS / Atom）"
            extra="推荐媒体官方 RSS 端点；第三方聚合端点（如公共 RSSHub）可用，上游授权与可用性风险自担。"
            rules={[{ required: true, message: "请输入 http(s) 订阅地址" }]}
          >
            <Input placeholder="https://example.com/rss.xml" allowClear />
          </Form.Item>
          <Form.Item label=" ">
            <Button
              type="primary"
              htmlType="submit"
              loading={addMutation.isPending}
              icon={<NotificationOutlined aria-hidden />}
            >
              添加订阅
            </Button>
          </Form.Item>
        </div>
      </Form>
      {feeds.length === 0 && !feedsQuery.isPending && (
        <Empty description="还没有订阅源：添加一个免费公开源后点击刷新即可看到新闻" />
      )}
      <ul className="news-feed-list">
        {feeds.map((feed) => (
          <li className="news-feed-row" key={feed.id}>
            <div className="news-feed-main">
              <strong>{feed.name}</strong>
              <FeedStatus feed={feed} />
              <span className="news-feed-count">{feed.item_count} 条</span>
              <span className="news-feed-time">
                最近成功：{formatIso(feed.last_success_at)}
              </span>
            </div>
            <span className="news-feed-url">{feed.url}</span>
            <Space>
              <Button
                size="small"
                icon={<ReloadOutlined aria-hidden />}
                loading={refreshMutation.isPending && refreshMutation.variables === feed.id}
                onClick={() => refreshMutation.mutate(feed.id)}
              >
                刷新
              </Button>
              <Switch
                aria-label={`启用或停用 ${feed.name}`}
                checked={feed.enabled}
                onChange={(checked) =>
                  updateMutation.mutate({ id: feed.id, enabled: checked })
                }
              />
              <Popconfirm
                title="删除订阅"
                description="同时删除已抓取的条目，确定？"
                okText="删除"
                cancelText="取消"
                onConfirm={() => deleteMutation.mutate(feed.id)}
              >
                <Button size="small" danger>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          </li>
        ))}
      </ul>

      <div className="section-heading">
        <h2>后台轮询</h2>
        <span>默认关闭；开启后仅自动抓取免费公开源，不产生任何付费调用</span>
      </div>
      <Space size="large" wrap>
        <Space>
          <Switch
            aria-label="开启或关闭后台轮询"
            checked={settingsQuery.data?.settings.poll_enabled ?? false}
            onChange={(checked) =>
              settingsMutation.mutate({
                poll_enabled: checked,
                interval_minutes: settingsQuery.data?.settings.interval_minutes ?? 30,
              })
            }
          />
          <Typography.Text>开启后台轮询</Typography.Text>
        </Space>
        <Space>
          <Typography.Text>刷新间隔</Typography.Text>
          <Select
            aria-label="轮询间隔"
            style={{ width: 140 }}
            value={settingsQuery.data?.settings.interval_minutes ?? 30}
            disabled={!settingsQuery.data?.settings.poll_enabled}
            options={[15, 30, 60, 120].map((minutes) => ({
              value: minutes,
              label: minutes >= 60 ? `${minutes / 60} 小时` : `${minutes} 分钟`,
            }))}
            onChange={(interval_minutes) =>
              settingsMutation.mutate({
                poll_enabled: settingsQuery.data?.settings.poll_enabled ?? false,
                interval_minutes,
              })
            }
          />
        </Space>
      </Space>

      <div className="section-heading">
        <h2>官方资讯（同花顺）</h2>
        <span>按已确认标的拉取基金资讯与个股异动解读；同花顺官方免费开放，限流以平台为准</span>
      </div>
      <Space wrap style={{ marginBottom: 16 }}>
        <Button
          icon={<NotificationOutlined aria-hidden />}
          loading={officialMutation.isPending}
          onClick={() => officialMutation.mutate()}
        >
          拉取官方资讯
        </Button>
        {officialItems.length > 0 && (
          <Typography.Text type="secondary">
            本次共 {officialItems.length} 条，仅本次展示，不落库
          </Typography.Text>
        )}
      </Space>
      {officialMutation.data?.warnings.map((warning) => (
        <Typography.Text key={warning} type="warning" style={{ display: "block" }}>
          {warning}
        </Typography.Text>
      ))}
      <ul className="news-timeline" style={{ marginBottom: 28 }}>
        {officialItems.map((item) => (
          <NewsLine key={item.id} item={item} />
        ))}
      </ul>

      <div className="section-heading">
        <h2>新闻时间线</h2>
        <span>新闻是线索：关键数字与事实需经第二来源交叉验证后才能作为依据</span>
      </div>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          aria-label="按来源过滤"
          style={{ width: 200 }}
          placeholder="全部来源"
          allowClear
          value={feedFilter}
          onChange={(value) => setFeedFilter(value ?? null)}
          options={feeds.map((feed) => ({ value: feed.id, label: feed.name }))}
        />
        <Select
          aria-label="按标的过滤"
          style={{ width: 220 }}
          placeholder="全部标的（已确认）"
          allowClear
          value={codeFilter}
          onChange={(value) => setCodeFilter(value ?? null)}
          options={identities.map((identity: { thscode: string; name: string }) => ({
            value: identity.thscode,
            label: `${identity.name} ${identity.thscode}`,
          }))}
        />
        <Button
          icon={<ReloadOutlined aria-hidden />}
          loading={itemsQuery.isFetching}
          onClick={() => refreshMutation.mutate(undefined)}
        >
          全部刷新
        </Button>
      </Space>
      {itemsQuery.isError && (
        <ErrorNotice
          error={itemsQuery.error}
          retry={() => void itemsQuery.refetch()}
        />
      )}
      {items.length === 0 && !itemsQuery.isPending && (
        <Empty description="暂无新闻：添加订阅并刷新后可见" />
      )}
      <ul className="news-timeline">
        {items.map((item) => (
          <NewsLine key={item.id} item={item}
          />
        ))}
      </ul>
    </>
  );
}
