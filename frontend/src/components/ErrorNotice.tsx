import { Alert, Button, Space } from "antd";
import { normalizeError } from "../api/client";

export default function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  const detail = normalizeError(error);
  return (
    <Alert
      type="error"
      showIcon
      title={detail.message}
      description={
        <Space orientation="vertical" size={4}>
          <span className="error-detail">
            {detail.category}
            {detail.request_id ? ` · 请求编号 ${detail.request_id}` : ""}
          </span>
          {retry && (
            <Button size="small" onClick={retry}>
              重试
            </Button>
          )}
        </Space>
      }
    />
  );
}
