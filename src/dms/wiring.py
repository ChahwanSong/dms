"""설정 기반 live 어댑터/리졸버 선택. cli/app 공용."""
from .execution import StubExecutionAdapter
from .identity_ldap import build_ldap_resolver


def build_identity_resolver(settings):
    return build_ldap_resolver(settings)


def build_execution_adapter(settings, repos):
    if settings.execution_backend != "volcano":
        return StubExecutionAdapter()
    from .execution_volcano import KubernetesClient, VolcanoExecutionAdapter

    def read_text(path):
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return None

    return VolcanoExecutionAdapter(
        KubernetesClient(settings.k8s_namespace),
        job_image=settings.job_image, namespace=settings.k8s_namespace,
        storages_lookup=lambda n: repos.storages.get(n), read_text=read_text,
        artifact_base=settings.artifact_base_uri)


def build_build_runner(settings):
    if settings.execution_backend != "volcano":
        from .build_runner import StubBuildRunner
        return StubBuildRunner()
    from .build_runner import BuildRunner
    from .execution_volcano import KubernetesClient
    return BuildRunner(KubernetesClient(settings.k8s_namespace),
                       namespace=settings.k8s_namespace,
                       registry=settings.build_registry,
                       builder_image=settings.build_builder_image,
                       timeout_seconds=settings.build_timeout_seconds)


def build_rollout_runner(settings):
    if settings.execution_backend != "volcano":
        from .rollout_runner import StubRolloutRunner
        return StubRolloutRunner()
    from .execution_volcano import KubernetesClient
    from .rollout_runner import RolloutRunner
    return RolloutRunner(KubernetesClient(settings.k8s_namespace),
                         namespace=settings.k8s_namespace)


def build_queue_reader(settings):
    # StubRolloutRunner 와 같은 선택 규칙(설계 §2.5): 기본 백엔드(stub)에서 스텁
    # 페어가 없으면 /api/admin/metrics/queue 가 모든 로컬·CI 에서 500 이다.
    if settings.execution_backend != "volcano":
        from .queue_reader import StubQueueReader
        return StubQueueReader()
    from .execution_volcano import KubernetesClient
    from .queue_reader import VolcanoQueueReader
    return VolcanoQueueReader(KubernetesClient(settings.k8s_namespace),
                              namespace=settings.k8s_namespace)
