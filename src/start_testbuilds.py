"""
Copyright 2019-2020 Amazon.com, Inc. or its affiliates. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License"). You
may not use this file except in compliance with the License. A copy of
the License is located at

    http://aws.amazon.com/apache2.0/

or in the "license" file accompanying this file. This file is
distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
ANY KIND, either express or implied. See the License for the specific
language governing permissions and limitations under the License.
"""

import json
import logging
import os
import sys

import boto3

import constants
import config
import utils

from codebuild_environment import get_codebuild_project_name


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.DEBUG)
LOGGER.addHandler(logging.StreamHandler(sys.stdout))
LOGGER.addHandler(logging.StreamHandler(sys.stderr))


def run_test_job(commit, codebuild_project, images_str=""):
    test_env_file = constants.TEST_ENV_PATH
    if not os.path.exists(test_env_file):
        raise FileNotFoundError(
            f"{test_env_file} not found. This is required to set test environment variables"
            f" for test jobs. Failing the build."
        )

    with open(test_env_file) as test_env_file:
        env_overrides = json.load(test_env_file)

    # For EC2 tests, if HEAVY_INSTANCE_EC2_TESTS_ENABLED is True, the test job will run tests on
    # large/expensive instance types as well as small/regular instance types, based on the config of
    # the test function. If False, the test job will only run tests on small/regular instance types.
    are_heavy_instance_ec2_tests_enabled = (
        config.are_heavy_instance_ec2_tests_enabled() and "ec2" in codebuild_project
    )

    # For EC2 tests, enable IPv6 testing when config is enabled
    is_ipv6_test_enabled = config.is_ipv6_test_enabled() and "ec2" in codebuild_project

    if config.is_deep_canary_mode_enabled():
        env_overrides.append({"name": "DEEP_CANARY_MODE", "value": "true", "type": "PLAINTEXT"})

    pr_num = os.getenv("PR_NUMBER")
    LOGGER.debug(f"pr_num {pr_num}")
    env_overrides.extend(
        [
            # Adding FRAMEWORK to env variables to enable simulation of deep canary tests in PR
            {"name": "FRAMEWORK", "value": os.getenv("FRAMEWORK", ""), "type": "PLAINTEXT"},
            # Adding IMAGE_TYPE to env variables to enable simulation of deep canary tests in PR
            {"name": "IMAGE_TYPE", "value": os.getenv("IMAGE_TYPE", ""), "type": "PLAINTEXT"},
            {"name": "DLC_IMAGES", "value": images_str, "type": "PLAINTEXT"},
            {"name": "PR_NUMBER", "value": pr_num, "type": "PLAINTEXT"},
            # NIGHTLY_PR_TEST_MODE is passed as an env variable here because it is more convenient to set this in
            # dlc_developer_config, and imports during test execution are less complicated when there are fewer
            # cross-references between test and src code.
            {
                "name": "NIGHTLY_PR_TEST_MODE",
                "value": str(config.is_nightly_pr_test_mode_enabled()),
                "type": "PLAINTEXT",
            },
            # USE_SCHEDULER is passed as an env variable here because it is more convenient to set this in
            # dlc_developer_config, compared to having another config file under dlc/tests/.
            {
                "name": "USE_SCHEDULER",
                "value": str(config.is_scheduler_enabled()),
                "type": "PLAINTEXT",
            },
            # SM_EFA_TEST_INSTANCE_TYPE is passed to SM test job to pick a matching instance type as defined by user
            {
                "name": "SM_EFA_TEST_INSTANCE_TYPE",
                "value": config.get_sagemaker_remote_efa_instance_type(),
                "type": "PLAINTEXT",
            },
            {
                "name": "IPV6_VPC_NAME",
                "value": config.get_ipv6_vpc_name(),
                "type": "PLAINTEXT",
            },
            {
                "name": "HEAVY_INSTANCE_EC2_TESTS_ENABLED",
                "value": str(are_heavy_instance_ec2_tests_enabled),
                "type": "PLAINTEXT",
            },
            {
                "name": "ENABLE_IPV6_TESTING",
                "value": str(is_ipv6_test_enabled),
                "type": "PLAINTEXT",
            },
            {
                "name": "FRAMEWORK_BUILDSPEC_FILE",
                "value": config.get_buildspec_override() or os.getenv("FRAMEWORK_BUILDSPEC_FILE"),
                "type": "PLAINTEXT",
            },
        ]
    )
    LOGGER.debug(f"env_overrides dict: {env_overrides}")

    client = boto3.client("codebuild")
    return client.start_build(
        projectName=codebuild_project,
        environmentVariablesOverride=env_overrides,
        sourceVersion=commit,
    )


def is_test_job_enabled(test_type):
    """
    Check to see if a test job is enabled
    See if we should run the tests based on test types and config options.
    """
    if test_type == constants.SAGEMAKER_REMOTE_TESTS and config.is_sm_remote_test_enabled():
        return True
    if test_type == constants.SAGEMAKER_EFA_TESTS and config.is_sm_efa_test_enabled():
        return True
    if test_type == constants.SAGEMAKER_RC_TESTS and config.is_sm_rc_test_enabled():
        return True
    if test_type == constants.SAGEMAKER_BENCHMARK_TESTS and config.is_sm_benchmark_test_enabled():
        return True
    if test_type == constants.EC2_TESTS and config.is_ec2_test_enabled():
        return True
    if test_type == constants.EC2_BENCHMARK_TESTS and config.is_ec2_benchmark_test_enabled():
        return True
    if test_type == constants.ECS_TESTS and config.is_ecs_test_enabled():
        return True
    if test_type == constants.EKS_TESTS and config.is_eks_test_enabled():
        return True
    if test_type == constants.SANITY_TESTS and config.is_sanity_test_enabled():
        return True
    if test_type == constants.SECURITY_TESTS and config.is_security_test_enabled():
        return True

    return False


def is_test_job_implemented_for_framework(images_str, test_type):
    """
    Check to see if a test job is implemented and supposed to be executed for this particular set of images
    """
    is_trcomp_image = False
    is_huggingface_trcomp_image = False
    is_huggingface_image = False
    if "huggingface" in images_str:
        if "trcomp" in images_str:
            is_huggingface_trcomp_image = True
        else:
            is_huggingface_image = True
    elif "trcomp" in images_str:
        is_trcomp_image = True

    is_autogluon_image = "autogluon" in images_str

    if (is_huggingface_image or is_autogluon_image) and test_type in [
        constants.EC2_TESTS,
        constants.EC2_BENCHMARK_TESTS,
        constants.ECS_TESTS,
        constants.EKS_TESTS,
    ]:
        LOGGER.debug(f"Skipping {test_type} test")
        return False
        # SM Training Compiler has EC2 tests implemented so don't skip
    if is_huggingface_trcomp_image and (
        test_type
        in [
            constants.ECS_TESTS,
            constants.EKS_TESTS,
            constants.EC2_BENCHMARK_TESTS,
        ]
    ):
        LOGGER.debug(f"Skipping {test_type} tests for huggingface trcomp containers")
        return False

    if is_trcomp_image and (
        test_type
        in [
            constants.EKS_TESTS,
            constants.EC2_BENCHMARK_TESTS,
        ]
    ):
        LOGGER.debug(f"Skipping {test_type} tests for trcomp containers")
        return False
    return True


def run_deep_canary_pr_testbuilds():
    """
    Deep Canaries can only be run on PyTorch or TensorFlow, Training or Inference, x86 or Graviton/
    ARM64 DLC images.
    This helper function determines whether this PR build job has been enabled, and this job has
    corresponding Deep Canaries that can be executed.
    If both these conditions are true, then it configures and launches a "dlc-pr-deep-canary-test"
    test job to test the specific framework, image-type, arch-type subset of Prod DLC images that
    match this PR build job.
    As a part of the setup, this function needs to create the TEST_TRIGGER env variable, and
    populate the constants.TEST_ENV_PATH file, which would normally have been done by image_builder
    after building images, if it had executed on this PR build job.
    If this PR build job is not enabled, then it does nothing.
    """
    build_framework = os.getenv("FRAMEWORK")
    general_builder_enabled = config.is_general_builder_enabled_for_this_pr_build(build_framework)
    graviton_builder_enabled = config.is_graviton_builder_enabled_for_this_pr_build(build_framework)
    arm64_builder_enabled = config.is_arm64_builder_enabled_for_this_pr_build(build_framework)
    if config.is_deep_canary_mode_enabled() and (
        general_builder_enabled or graviton_builder_enabled or arm64_builder_enabled
    ):
        commit = os.getenv("CODEBUILD_RESOLVED_SOURCE_VERSION")
        # Write TEST_TRIGGER to TEST_ENV_PATH because image_builder wasn't run.
        test_env_variables = [
            {"name": "TEST_TRIGGER", "value": get_codebuild_project_name(), "type": "PLAINTEXT"},
        ]
        utils.write_to_json_file(constants.TEST_ENV_PATH, test_env_variables)
        test_type = "deep-canary"
        LOGGER.debug(f"test_type : {test_type}")
        pr_test_job = f"dlc-pr-{test_type}-test"
        if graviton_builder_enabled:
            pr_test_job += "-graviton"
        elif arm64_builder_enabled:
            pr_test_job += "-arm64"
        run_test_job(commit, pr_test_job)


def main():
    build_context = os.getenv("BUILD_CONTEXT")
    if build_context != "PR":
        LOGGER.info(f"Not triggering test jobs from boto3, as BUILD_CONTEXT is {build_context}")
        return

    if config.is_deep_canary_mode_enabled():
        run_deep_canary_pr_testbuilds()
        # Skip all other tests on this PR if deep_canary_mode is true
        # If deep_canary_mode is true, then all tests are skipped on build jobs incompatible with
        # Deep Canaries, as detailed in the docstring for run_deep_canary_pr_testbuilds().
        return

    # load the images for all test_types to pass on to code build jobs
    with open(constants.TEST_TYPE_IMAGES_PATH) as json_file:
        test_images = json.load(json_file)

    # Run necessary PR test jobs
    commit = os.getenv("CODEBUILD_RESOLVED_SOURCE_VERSION")

    for test_type, images in test_images.items():
        # only run the code build test jobs when the images are present
        LOGGER.debug(f"test_type : {test_type}")
        LOGGER.debug(f"images: {images}")
        if images:
            pr_test_job = f"dlc-pr-{test_type}-test"
            images_str = " ".join(images)

            # Maintaining separate codebuild projects for graviton/arm64 sanity and security tests
            if test_type == constants.SANITY_TESTS or test_type == constants.SECURITY_TESTS:
                if "graviton" in images_str:
                    pr_test_job += "-graviton"
                elif "arm64" in images_str:
                    pr_test_job += "-arm64"

            if is_test_job_enabled(test_type) and is_test_job_implemented_for_framework(
                images_str, test_type
            ):
                run_test_job(commit, pr_test_job, images_str)

            if test_type == "autopr" and config.is_autopatch_build_enabled(
                buildspec_path=config.get_buildspec_override()
                or os.getenv("FRAMEWORK_BUILDSPEC_FILE"),
            ):
                run_test_job(commit, f"dlc-pr-{test_type}", images_str)

            # Trigger sagemaker local test jobs when there are changes in sagemaker_tests
            if test_type == "sagemaker" and config.is_sm_local_test_enabled():
                test_job = f"dlc-pr-{test_type}-local-test"
                run_test_job(commit, test_job, images_str)


if __name__ == "__main__":
    main()
