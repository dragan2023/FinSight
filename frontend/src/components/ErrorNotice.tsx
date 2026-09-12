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
