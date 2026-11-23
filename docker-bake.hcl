# The image, for the platforms the hubs run, with the repo link on the multi-arch index (GitHub
# reads org.opencontainers.image.source there to connect the package to this repo; a label alone
# lands only on each platform's image). The manifest has no version key: the tag is the release.
#
#   TAG=0.2.0 docker buildx bake --push      # ghcr.io/slowboard-io/slowapp-calendar:0.2.0 and :latest
#   docker buildx bake test --load            # one platform, to try locally
#
# --push needs a docker-container builder (docker buildx create --use --driver docker-container).

variable "TAG" { default = "dev" }
variable "IMAGE" { default = "ghcr.io/slowboard-io/slowapp-calendar" }
variable "SOURCE" { default = "https://github.com/slowboard-io/slowapp-calendar" }

target "default" {
  context     = "."
  platforms   = ["linux/amd64", "linux/arm64"]
  tags        = ["${IMAGE}:${TAG}", "${IMAGE}:latest"]
  labels      = { "org.opencontainers.image.source" = SOURCE }
  annotations = ["index:org.opencontainers.image.source=${SOURCE}", "manifest:org.opencontainers.image.source=${SOURCE}"]
}

target "test" {
  inherits    = ["default"]
  platforms   = []
  tags        = ["slowapp-calendar:test"]
  annotations = []
}
