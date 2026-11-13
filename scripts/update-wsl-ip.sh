#!/bin/bash
# ============================================================
# WSL IP 自动更新脚本
# ============================================================
# 作用：
#   检测是否在 WSL 环境中，如果是则自动获取 WSL IP，
#   更新 .env 中的 S3_PUBLIC_ENDPOINT，解决 WSL localhost
#   转发不工作时图片无法加载的问题。
#
# 使用方式：
#   ./scripts/update-wsl-ip.sh          # 自动检测并更新
#   SKIP_WSL_IP=1 ./scripts/update-wsl-ip.sh  # 跳过更新
#
# 返回值：
#   0 - 更新成功或被跳过
#   1 - 出错
# ============================================================

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# 项目根目录
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
# .env 文件路径
ENV_FILE="$PROJECT_ROOT/.env"
# .env.example 路径
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"

# 检测是否在 WSL 环境中
is_wsl() {
    # 方法1：检查 /proc/sys/fs/binfmt_misc/WSLInterop
    if [ -f /proc/sys/fs/binfmt_misc/WSLInterop ]; then
        return 0
    fi
    # 方法2：检查内核版本包含 microsoft
    if uname -r | grep -qi microsoft; then
        return 0
    fi
    return 1
}

# 获取 WSL 的 IPv4 地址
get_wsl_ip() {
    # 优先从 eth0 获取
    local ip
    ip=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
    if [ -n "$ip" ]; then
        echo "$ip"
        return 0
    fi

    # 备选：从其他网络接口获取
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -n "$ip" ]; then
        echo "$ip"
        return 0
    fi

    return 1
}

# 更新 .env 文件中的变量
# $1: 变量名
# $2: 新值
update_env_var() {
    local var_name="$1"
    local new_value="$2"

    if grep -q "^${var_name}=" "$ENV_FILE"; then
        # 变量存在，替换它
        sed -i "s|^${var_name}=.*|${var_name}=${new_value}|" "$ENV_FILE"
    else
        # 变量不存在，追加到文件末尾
        echo "${var_name}=${new_value}" >> "$ENV_FILE"
    fi
}

# ============ 主逻辑 ============

# 检查是否跳过
if [ "${SKIP_WSL_IP:-}" = "1" ]; then
    echo -e "${YELLOW}⏭️  SKIP_WSL_IP=1，跳过 WSL IP 检测${NC}"
    exit 0
fi

# 检测是否在 WSL 中
if ! is_wsl; then
    # 不是 WSL 环境，静默退出
    exit 0
fi

echo -e "${BLUE}🐧 检测到 WSL 环境，正在获取 IP 地址...${NC}"

# 获取 WSL IP
WSL_IP=$(get_wsl_ip)
if [ -z "$WSL_IP" ]; then
    echo -e "${RED}❌ 无法获取 WSL IP 地址${NC}"
    echo -e "请检查网络接口是否正常。"
    exit 1
fi

echo -e "当前 WSL IP: ${GREEN}${WSL_IP}${NC}"

# 确保 .env 文件存在
if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        echo -e "${YELLOW}⚠️  .env 文件不存在，从 .env.example 复制...${NC}"
        cp "$ENV_EXAMPLE" "$ENV_FILE"
    else
        echo -e "${RED}❌ .env 和 .env.example 都不存在${NC}"
        exit 1
    fi
fi

# 更新 S3_PUBLIC_ENDPOINT（浏览器访问图片的地址）
S3_PUBLIC="http://${WSL_IP}:7313"
update_env_var "S3_PUBLIC_ENDPOINT" "$S3_PUBLIC"
echo -e "${GREEN}✅ S3_PUBLIC_ENDPOINT 已更新为 ${S3_PUBLIC}${NC}"

# 输出 WSL IP 供其他脚本使用
echo "WSL_IP=${WSL_IP}"

exit 0
