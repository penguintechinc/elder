#!/bin/bash
# Generate Python gRPC code from protobuf schemas

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Generating Python gRPC code from protobuf schemas...${NC}"

# Proto source directory
PROTO_DIR="apps/api/grpc/proto"
# Generated code output directory
OUTPUT_DIR="apps/api/grpc/generated"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Create __init__.py in generated directory
touch "$OUTPUT_DIR/__init__.py"

# Generate Python code for each proto file
echo -e "${GREEN}Generating common.proto...${NC}"
python3 -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    "$PROTO_DIR/common.proto"

echo -e "${GREEN}Generating organization.proto...${NC}"
python3 -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    "$PROTO_DIR/organization.proto"

echo -e "${GREEN}Generating entity.proto...${NC}"
python3 -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    "$PROTO_DIR/entity.proto"

echo -e "${GREEN}Generating dependency.proto...${NC}"
python3 -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    "$PROTO_DIR/dependency.proto"

echo -e "${GREEN}Generating graph.proto...${NC}"
python3 -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    "$PROTO_DIR/graph.proto"

echo -e "${GREEN}Generating auth.proto...${NC}"
python3 -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    "$PROTO_DIR/auth.proto"

echo -e "${GREEN}Generating elder.proto (main service)...${NC}"
python3 -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    "$PROTO_DIR/elder.proto"

# Fix imports in generated files (proto imports need to be relative)
echo -e "${BLUE}Fixing imports in generated files...${NC}"
cd "$OUTPUT_DIR"
for file in *.py; do
    # Replace 'import common_pb2' with 'from . import common_pb2'
    sed -i 's/^import \([a-z_]*\)_pb2/from . import \1_pb2/g' "$file" 2>/dev/null || true
done
cd - > /dev/null

echo -e "${GREEN}✓ Python gRPC code generation complete!${NC}"
echo -e "${BLUE}Generated files in: $OUTPUT_DIR${NC}"
ls -lh "$OUTPUT_DIR"
