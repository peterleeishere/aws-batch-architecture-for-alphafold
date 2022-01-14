"""
Helper functions for the AWS-Alphafold notebook.
"""
from datetime import datetime
import boto3
import uuid
import sagemaker
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio import AlignIO
from Bio.Align import MultipleSeqAlignment
import os
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import colors
import numpy as np
import string
from string import ascii_uppercase, ascii_lowercase
import py3Dmol

boto_session = boto3.session.Session()
sm_session = sagemaker.session.Session()
region = boto_session.region_name
s3 = boto3.client("s3", region_name=region)
batch = boto3.client("batch", region_name=region)
cfn = boto3.client("cloudformation", region_name=region)
logs_client = boto3.client("logs")


pymol_color_list = [
    "#33ff33",
    "#00ffff",
    "#ff33cc",
    "#ffff00",
    "#ff9999",
    "#e5e5e5",
    "#7f7fff",
    "#ff7f00",
    "#7fff7f",
    "#199999",
    "#ff007f",
    "#ffdd5e",
    "#8c3f99",
    "#b2b2b2",
    "#007fff",
    "#c4b200",
    "#8cb266",
    "#00bfbf",
    "#b27f7f",
    "#fcd1a5",
    "#ff7f7f",
    "#ffbfdd",
    "#7fffff",
    "#ffff7f",
    "#00ff7f",
    "#337fcc",
    "#d8337f",
    "#bfff3f",
    "#ff7fff",
    "#d8d8ff",
    "#3fffbf",
    "#b78c4c",
    "#339933",
    "#66b2b2",
    "#ba8c84",
    "#84bf00",
    "#b24c66",
    "#7f7f7f",
    "#3f3fa5",
    "#a5512b",
]

alphabet_list = list(ascii_uppercase + ascii_lowercase)


def create_job_name(suffix=None):

    """
    Define a simple job identifier
    """

    if suffix == None:
        return datetime.now().strftime("%Y%m%dT%H%M%S")
    else:
        ## Ensure that the suffix conforms to the Batch requirements, (only letters,
        ## numbers, hyphens, and underscores are allowed).
        suffix = sub("\W", "_", suffix)
        return datetime.now().strftime("%Y%m%dT%H%M%S") + "_" + suffix


def upload_fasta_to_s3(
    sequences,
    ids,
    bucket=sm_session.default_bucket(),
    job_name=uuid.uuid4(),
    region="us-east-1",
):

    """
    Create a fasta file and upload it to S3.
    """

    file_out = "_tmp.fasta"
    with open(file_out, "a") as f_out:
        for i, seq in enumerate(sequences):
            seq_record = SeqRecord(Seq(seq), id=ids[i])
            SeqIO.write(seq_record, f_out, "fasta")

    object_key = f"{job_name}/{job_name}.fasta"
    response = s3.upload_file(file_out, bucket, object_key)
    os.remove(file_out)
    s3_uri = f"s3://{bucket}/{object_key}"
    print(f"Sequence file uploaded to {s3_uri}")
    return object_key


def list_alphafold_stacks():
    af_stacks = []
    for stack in cfn.list_stacks(
        StackStatusFilter=["CREATE_COMPLETE", "UPDATE_COMPLETE"]
    )["StackSummaries"]:
        if "Alphafold on AWS Batch" in stack["TemplateDescription"]:
            af_stacks.append(stack)
    return(af_stacks)

def get_batch_resources(stack_name):
    """
    Get the resource names of the Batch resources for running Alphafold jobs.
    """
    
    # stack_name = af_stacks[0]["StackName"]
    stack_resources = cfn.list_stack_resources(StackName=stack_name)
    for resource in stack_resources["StackResourceSummaries"]:
        if resource["LogicalResourceId"] == "GPUFoldingJobDefinition":
            gpu_job_definition = resource["PhysicalResourceId"]
        if resource["LogicalResourceId"] == "PrivateGPUJobQueue":
            gpu_job_queue = resource["PhysicalResourceId"]
        if resource["LogicalResourceId"] == "CPUFoldingJobDefinition":
            cpu_job_definition = resource["PhysicalResourceId"]
        if resource["LogicalResourceId"] == "PrivateCPUJobQueue":
            cpu_job_queue = resource["PhysicalResourceId"]
        if resource["LogicalResourceId"] == "CPUDownloadJobDefinition":
            download_job_definition = resource["PhysicalResourceId"]
        if resource["LogicalResourceId"] == "PublicCPUJobQueue":
            download_job_queue = resource["PhysicalResourceId"]
    return {
        "gpu_job_definition": gpu_job_definition,
        "gpu_job_queue": gpu_job_queue,
        "cpu_job_definition": cpu_job_definition,
        "cpu_job_queue": cpu_job_queue,
        "download_job_definition": download_job_definition,
        "download_job_queue": download_job_queue,
    }


def get_batch_job_info(jobId):

    """
    Retrieve and format information about a batch job.
    """

    job_description = batch.describe_jobs(jobs=[jobId])

    output = {
        "jobArn": job_description["jobs"][0]["jobArn"],
        "jobName": job_description["jobs"][0]["jobName"],
        "jobId": job_description["jobs"][0]["jobId"],
        "status": job_description["jobs"][0]["status"],
        "createdAt": datetime.utcfromtimestamp(
            job_description["jobs"][0]["createdAt"] / 1000
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dependsOn": job_description["jobs"][0]["dependsOn"],
        "tags": job_description["jobs"][0]["tags"],
    }

    if output["status"] in ["STARTING", "RUNNING", "SUCCEEDED", "FAILED"]:
        output["logStreamName"] = job_description["jobs"][0]["container"][
            "logStreamName"
        ]
    return output


def get_batch_logs(logStreamName):

    """
    Retrieve and format logs for batch job.
    """

    try:
        response = logs_client.get_log_events(
            logGroupName="/aws/batch/job", logStreamName=logStreamName
        )
    except logs_client.meta.client.exceptions.ResourceNotFoundException:
        return f"Log stream {logStreamName} does not exist. Please try again in a few minutes"

    logs = pd.DataFrame.from_dict(response["events"])
    logs.timestamp = logs.timestamp.transform(
        lambda x: datetime.fromtimestamp(x / 1000)
    )
    logs.drop("ingestionTime", axis=1, inplace=True)
    return logs


def plot_msa(bucket, job_name):

    if not os.path.exists("data"):
        os.makedirs("data")

    s3.download_file(
        bucket, f"{job_name}/msas/mgnify_hits.sto", "data/mgnify_hits.sto"
    )
    s3.download_file(
        bucket,
        f"{job_name}/msas/small_bfd_hits.sto",
        "data/small_bfd_hits.sto",
    )
    s3.download_file(
        bucket,
        f"{job_name}/msas/uniref90_hits.sto",
        "data/uniref90_hits.sto",
    )

    msas = [
        AlignIO.read("data/mgnify_hits.sto", "stockholm"),
        AlignIO.read("data/small_bfd_hits.sto", "stockholm"),
        AlignIO.read("data/uniref90_hits.sto", "stockholm"),
    ]
    full_single_chain_msa = []
    for msa in msas:
        for single_chain_msa in msa:
            full_single_chain_msa.append(single_chain_msa.seq)

    deduped_full_single_chain_msa = list(dict.fromkeys(full_single_chain_msa))
    total_msa_size = len(deduped_full_single_chain_msa)
    aa_map = {res: i for i, res in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ-")}
    msa_arr = np.array(
        [[aa_map[aa] for aa in seq] for seq in deduped_full_single_chain_msa]
    )
    plt.figure(figsize=(12, 3))
    plt.title(f"Per-Residue Count of Non-Gap Amino Acids in the MSA for Sequence")
    plt.plot(np.sum(msa_arr != aa_map["-"], axis=0), color="black")
    plt.ylabel("Non-Gap Count")
    plt.yticks(range(0, total_msa_size + 1, max(1, int(total_msa_size / 3))))
    plt.show()
    pass


def display_structure(
    bucket,
    job_name,
    color="lDDT",
    show_sidechains=False,
    show_mainchains=False,
    chains=1,
    vmin=0.5,
    vmax=0.9,
):
    """
    Display the predicted structure in a Jupyter notebook cell
    """
    if color not in ["chain", "lDDT", "rainbow"]:
        raise ValueError("Color must be 'LDDT' (default), 'chain', or 'rainbow'")

    print(f"Downloading PDB file from s3://{bucket}/{job_name}/ranked_0.pdb")
    s3.download_file(bucket, f"{job_name}/ranked_0.pdb", "data/ranked_0.pdb")
    plot_pdb(
        "data/ranked_0.pdb",
        show_sidechains=show_sidechains,
        show_mainchains=show_mainchains,
        color=color,
        chains=chains,
        vmin=vmin,
        vmax=vmax,
    ).show()
    if color == "lDDT":
        plot_plddt_legend().show()


def plot_pdb(
    pred_output_path,
    show_sidechains=False,
    show_mainchains=False,
    color="lDDT",
    chains=None,
    Ls=None,
    vmin=0.5,
    vmax=0.9,
    color_HP=False,
    size=(800, 480),
):

    """
    Create a 3D view of a pdb structure
    Copied from https://github.com/sokrypton/ColabFold/blob/main/beta/colabfold.py
    """

    if chains is None:
        chains = 1 if Ls is None else len(Ls)

    view = py3Dmol.view(
        js="https://3dmol.org/build/3Dmol.js", width=size[0], height=size[1]
    )
    view.addModel(read_pdb_renum(pred_output_path, Ls), "pdb")
    if color == "lDDT":
        view.setStyle(
            {
                "cartoon": {
                    "colorscheme": {
                        "prop": "b",
                        "gradient": "roygb",
                        "min": vmin,
                        "max": vmax,
                    }
                }
            }
        )
    elif color == "rainbow":
        view.setStyle({"cartoon": {"color": "spectrum"}})
    elif color == "chain":
        for n, chain, color in zip(range(chains), alphabet_list, pymol_color_list):
            view.setStyle({"chain": chain}, {"cartoon": {"color": color}})
    if show_sidechains:
        BB = ["C", "O", "N"]
        HP = [
            "ALA",
            "GLY",
            "VAL",
            "ILE",
            "LEU",
            "PHE",
            "MET",
            "PRO",
            "TRP",
            "CYS",
            "TYR",
        ]
        if color_HP:
            view.addStyle(
                {"and": [{"resn": HP}, {"atom": BB, "invert": True}]},
                {"stick": {"colorscheme": "yellowCarbon", "radius": 0.3}},
            )
            view.addStyle(
                {"and": [{"resn": HP, "invert": True}, {"atom": BB, "invert": True}]},
                {"stick": {"colorscheme": "whiteCarbon", "radius": 0.3}},
            )
            view.addStyle(
                {"and": [{"resn": "GLY"}, {"atom": "CA"}]},
                {"sphere": {"colorscheme": "yellowCarbon", "radius": 0.3}},
            )
            view.addStyle(
                {"and": [{"resn": "PRO"}, {"atom": ["C", "O"], "invert": True}]},
                {"stick": {"colorscheme": "yellowCarbon", "radius": 0.3}},
            )
        else:
            view.addStyle(
                {
                    "and": [
                        {"resn": ["GLY", "PRO"], "invert": True},
                        {"atom": BB, "invert": True},
                    ]
                },
                {"stick": {"colorscheme": f"WhiteCarbon", "radius": 0.3}},
            )
            view.addStyle(
                {"and": [{"resn": "GLY"}, {"atom": "CA"}]},
                {"sphere": {"colorscheme": f"WhiteCarbon", "radius": 0.3}},
            )
            view.addStyle(
                {"and": [{"resn": "PRO"}, {"atom": ["C", "O"], "invert": True}]},
                {"stick": {"colorscheme": f"WhiteCarbon", "radius": 0.3}},
            )
    if show_mainchains:
        BB = ["C", "O", "N", "CA"]
        view.addStyle(
            {"atom": BB}, {"stick": {"colorscheme": f"WhiteCarbon", "radius": 0.3}}
        )
    view.zoomTo()
    return view


def plot_plddt_legend(dpi=100):

    """
    Create 3D Plot legend
    Copied from https://github.com/sokrypton/ColabFold/blob/main/beta/colabfold.py
    """

    thresh = [
        "plDDT:",
        "Very low (<50)",
        "Low (60)",
        "OK (70)",
        "Confident (80)",
        "Very high (>90)",
    ]
    plt.figure(figsize=(1, 0.1), dpi=dpi)
    ########################################
    for c in ["#FFFFFF", "#FF0000", "#FFFF00", "#00FF00", "#00FFFF", "#0000FF"]:
        plt.bar(0, 0, color=c)
    plt.legend(
        thresh,
        frameon=False,
        loc="center",
        ncol=6,
        handletextpad=1,
        columnspacing=1,
        markerscale=0.5,
    )
    plt.axis(False)
    return plt


def read_pdb_renum(pdb_filename, Ls=None):

    """
    Process pdb file.
    Copied from https://github.com/sokrypton/ColabFold/blob/main/beta/colabfold.py
    """

    if Ls is not None:
        L_init = 0
        new_chain = {}
        for L, c in zip(Ls, alphabet_list):
            new_chain.update({i: c for i in range(L_init, L_init + L)})
            L_init += L
    n, pdb_out = 1, []
    resnum_, chain_ = 1, "A"
    for line in open(pdb_filename, "r"):
        if line[:4] == "ATOM":
            chain = line[21:22]
            resnum = int(line[22 : 22 + 5])
            if resnum != resnum_ or chain != chain_:
                resnum_, chain_ = resnum, chain
                n += 1
            if Ls is None:
                pdb_out.append("%s%4i%s" % (line[:22], n, line[26:]))
            else:
                pdb_out.append(
                    "%s%s%4i%s" % (line[:21], new_chain[n - 1], n, line[26:])
                )
    return "".join(pdb_out)


def plot_msa_info(msa):

    """
    Plot a representation of the MSA coverage.
    Copied from https://github.com/sokrypton/ColabFold/blob/main/beta/colabfold.py
    """

    msa_arr = np.unique(msa, axis=0)
    total_msa_size = len(msa_arr)
    print(f"\n{total_msa_size} Sequences Found in Total\n")

    if total_msa_size > 1:
        plt.figure(figsize=(8, 5), dpi=100)
        plt.title("Sequence coverage")
        seqid = (msa[0] == msa_arr).mean(-1)
        seqid_sort = seqid.argsort()
        non_gaps = (msa_arr != 20).astype(float)
        non_gaps[non_gaps == 0] = np.nan
        plt.imshow(
            non_gaps[seqid_sort] * seqid[seqid_sort, None],
            interpolation="nearest",
            aspect="auto",
            cmap="rainbow_r",
            vmin=0,
            vmax=1,
            origin="lower",
            extent=(0, msa_arr.shape[1], 0, msa_arr.shape[0]),
        )
        plt.plot((msa_arr != 20).sum(0), color="black")
        plt.xlim(0, msa_arr.shape[1])
        plt.ylim(0, msa_arr.shape[0])
        plt.colorbar(
            label="Sequence identity to query",
        )
        plt.xlabel("Positions")
        plt.ylabel("Sequences")
        plt.show()
    else:
        print("Unable to display MSA of length 1")


def submit_batch_alphafold_job(
    job_name,
    fasta_paths,
    s3_bucket,
    is_prokaryote_list=None,
    data_dir="/mnt/data_dir/fsx",
    output_dir="alphafold",
    uniref90_database_path="/mnt/uniref90_database_path/uniref90.fasta",
    mgnify_database_path="/mnt/mgnify_database_path/mgy_clusters_2018_12.fa",
    small_bfd_database_path="/mnt/small_bfd_database_path/bfd-first_non_consensus_sequences.fasta",
    pdb70_database_path="/mnt/pdb70_database_path/pdb70",
    template_mmcif_dir="/mnt/template_mmcif_dir/mmcif_files",
    max_template_date=datetime.now().strftime("%Y-%m-%d"),
    obsolete_pdbs_path="/mnt/obsolete_pdbs_path/obsolete.dat",
    db_preset="reduced_dbs",
    model_preset="monomer",
    benchmark=False,
    use_precomputed_msas=False,
    features_paths=None,
    run_features_only=False,
    logtostderr=True,
    cpu=4,
    memory=16,
    gpu=1,
    depends_on=None,
    stack_name = None,
):

    if stack_name is None:
        stack_name = list_alphafold_stacks()[0]["StackName"]
    batch_resources = get_batch_resources(stack_name)

    container_overrides = {
        "command": [
            f"--fasta_paths={fasta_paths}",
            f"--uniref90_database_path={uniref90_database_path}",
            f"--mgnify_database_path={mgnify_database_path}",
            f"--pdb70_database_path={pdb70_database_path}",
            f"--small_bfd_database_path={small_bfd_database_path}",
            f"--data_dir={data_dir}",
            f"--template_mmcif_dir={template_mmcif_dir}",
            f"--obsolete_pdbs_path={obsolete_pdbs_path}",
            f"--output_dir={output_dir}",
            f"--max_template_date={max_template_date}",
            f"--db_preset={db_preset}",
            f"--model_preset={model_preset}",
            f"--s3_bucket={s3_bucket}",
        ],
        "resourceRequirements": [
            {"value": str(cpu), "type": "VCPU"},
            {"value": str(memory * 1000), "type": "MEMORY"},
        ],
    }

    if is_prokaryote_list is not None:
        container_overrides["command"].append(f"--is_prokaryote_list={is_prokaryote_list}")

    if benchmark:
        container_overrides["command"].append("--benchmark")

    if use_precomputed_msas:
        container_overrides["command"].append("--use_precomputed_msas")

    if features_paths is not None:
        container_overrides["command"].append(f"--features_paths={features_paths}")

    if run_features_only:
        container_overrides["command"].append("--run_features_only")

    if logtostderr:
        container_overrides["command"].append("--logtostderr")

    if gpu > 0:
        job_definition = batch_resources["gpu_job_definition"]
        job_queue = batch_resources["gpu_job_queue"]
        container_overrides["resourceRequirements"].append(
            {"value": str(gpu), "type": "GPU"}
        )
    else:
        job_definition = batch_resources["cpu_job_definition"]
        job_queue = batch_resources["cpu_job_queue"]

    print(container_overrides)
    if depends_on is None:
        response = batch.submit_job(
            jobDefinition=job_definition,
            jobName=job_name,
            jobQueue=job_queue,
            containerOverrides=container_overrides,
        )
    else:
        response = batch.submit_job(
            jobDefinition=job_definition,
            jobName=job_name,
            jobQueue=job_queue,
            containerOverrides=container_overrides,
            dependsOn=[{"jobId": depends_on, "type": "SEQUENTIAL"}],
        )

    return response

def submit_download_data_job(
    job_name="download_job",
    script="scripts/download_all_data.sh",
    cpu=4,
    memory=16,
    stack_name = None,
    download_dir = "/fsx",
    download_mode = "reduced_db"
    ):
    
    if stack_name is None:
        stack_name = list_alphafold_stacks()[0]["StackName"]
    batch_resources = get_batch_resources(stack_name)

    job_definition = batch_resources["download_job_definition"]
    job_queue = batch_resources["download_job_queue"]

    container_overrides = {
        "command": [
            script,
            download_dir,
            download_mode         
        ],
        "resourceRequirements": [
            {"value": str(cpu), "type": "VCPU"},
            {"value": str(memory * 1000), "type": "MEMORY"},
        ],
    }

    response = batch.submit_job(
        jobDefinition=job_definition,
        jobName=job_name,
        jobQueue=job_queue,
        containerOverrides=container_overrides,
    )

    return response